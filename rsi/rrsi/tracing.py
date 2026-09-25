"""Per-iteration trace of an RRSI run (``out_dir/trace.jsonl``, rendered by :func:`rsi.trace.inspect`).

:class:`RRSITrace` maps every step of Algorithm 1 / Algorithm 2 onto the uniform
:mod:`rsi.trace` event kinds::

    run_start    config, switches, K / K_str, splits, LLM roles, b_t schedule, gates, the
                 proposer system prompt and constitution (the stable prompt prefix, once)
    baseline     Evaluate(H_0, D_evolve, k): per-task means and raw trial rewards
    noise        delta (calibration method, z, sd_null, bootstrap se, warning)
    round_start  incumbent, S*, delta, trajectory, b_t (+ formula inputs), sigma_t (+ the stall
                 arithmetic), T_t / U_t, reserved variants, B_t with g_t, history / scoreboard sizes
    analysis     F_t (failure modes, capability gaps, success habits), the traces it read,
                 and every digester / analyst LLM exchange
    proposal     every proposer call (initial, done()-contract bounces, critic repairs): the
                 variable part of the prompt, the reply, the declared edits, the ACTUAL diff vs H_t
    critic       every critic verdict (stage, reasons, LLM exchange), the reserved-slot check
    note         tagging (declared vs diff-normalized components) and the liveness smoke
    eval         Evaluate(H', D_evolve, k): per-task means, raw trials, tokens, per-task change
    gate         Algorithm 2 per candidate with the numbers (S', S_t, S*, delta, floor, dS, dC,
                 nu, band branch, beta0 + beta1*dS, shaped score) and every gate's verdict
    decision     winner (argmax S' over admissible) or none; incumbent before / after; S*
    state        loop state after the round
    monitor      :class:`rsi.trace.ShadowMonitor` on sealed holdout / ood for each new incumbent
    run_end      stop reason, final incumbent, trajectory, usage and spend

The trace is WRITE-ONLY: nothing here is read back by the loop, every call is wrapped so
that a tracing error can never change a decision, and the shadow monitor runs on a copy of
the task model with its own usage meter (its spend never enters the loop's Budget).
Domains may expose ``audit_truth(artifact) -> dict`` (e.g. HarnessWorld's analytic expected
scores); it is recorded next to each evaluation as report-only ground truth.
"""
from __future__ import annotations

import copy
import functools
import hashlib
import math
from typing import Any, Optional, Sequence

from ..core.gates import GateContext
from ..core.llm import LLM, CachedLLM, LLMResponse, MockLLM
from ..core.tasks import TaskSuite
from ..trace import RunTracer, ShadowMonitor
from .evaluate import Measurement, relative_cost_change


def sha(text: str) -> str:
    return hashlib.sha256((text or "").encode()).hexdigest()[:12]


# ------------------------------------------------------------------ isolated task model --
class _SeparateMeter(LLM):
    """Same backend, separate usage meter (calls ``inner._complete`` so ``inner.meter`` is untouched)."""

    def __init__(self, inner: LLM) -> None:
        super().__init__()
        self.inner = inner
        self.name = inner.name

    def _complete(self, prompt, *, system, max_tokens, seed) -> LLMResponse:
        return self.inner._complete(prompt, system=system, max_tokens=max_tokens, seed=seed)


def isolated_llm(llm: Optional[LLM]) -> Optional[LLM]:
    """A view of ``llm`` whose calls are metered separately, so shadow-monitor rollouts never
    count toward the loop's usage or Budget. Cached backends share the cache directory (the
    monitor's sealed-split prompts cannot collide with the loop's evolve prompts), mocks get a
    fresh call counter and call log."""
    if llm is None:
        return None
    if isinstance(llm, CachedLLM):
        return CachedLLM(isolated_llm(llm.inner), llm.dir, offline=llm.offline)
    if isinstance(llm, MockLLM):
        return MockLLM(llm.responder, name=llm.name)
    return _SeparateMeter(llm)


def sealed_view(domain, splits: Sequence[str]):
    """A shallow copy of ``domain`` whose task suite holds ONLY the given sealed splits (a fresh
    :class:`TaskSuite`). The shadow monitor evaluates through it, so the loop's own suite object is
    never asked for a sealed split: loop reads and monitor reads stay separable (an audit of the
    loop's split access sees evolve only) and the monitor cannot see evolve tasks at all."""
    ids = {s: list(domain.tasks.splits[s]) for s in splits if s in domain.tasks.splits}
    tasks = [domain.tasks.tasks[i] for s in ids for i in ids[s]]
    view = copy.copy(domain)
    view.tasks = TaskSuite(list({t.id: t for t in tasks}.values()), ids, name=f"{domain.tasks.name}:shadow")
    return view


# ------------------------------------------------------------------------ payload helpers --
def eval_payload(ev: Measurement) -> dict:
    """Summary + per-task means + raw per-trial rewards and tokens of a stored measurement."""
    errors = sum(1 for trs in (ev.trials or {}).values() for tr in trs if tr.get("error"))
    summary = {"split": ev.split, "job": ev.job, "S": ev.S, "C": ev.C, "n_tasks": len(ev.per_task), "k": ev.k,
               "n_expected": ev.n_expected, "missing": ev.missing, "errors": errors,
               "steps": (ev.extra or {}).get("steps"), "error_rate": (ev.extra or {}).get("error_rate"),
               "families": (ev.extra or {}).get("families"), "artifact": (ev.artifact_id or "")[:10],
               "seeds": ev.seeds}
    return {"summary": summary, "per_task": ev.task_means(),
            "trials": {t: list(r.rewards) for t, r in ev.per_task.items()},
            "tokens": {t: list(r.tokens) for t, r in ev.per_task.items()}}


def per_task_change(inc: Measurement, cand: Measurement) -> dict:
    im, cm = inc.task_means(), cand.task_means()
    up = {t: round(cm[t] - im[t], 4) for t in cm if t in im and cm[t] > im[t] + 1e-12}
    down = {t: round(cm[t] - im[t], 4) for t in cm if t in im and cm[t] < im[t] - 1e-12}
    return {"improved": up, "regressed": down, "n_improved": len(up), "n_regressed": len(down),
            "n_same": len(cm) - len(up) - len(down)}


def report_text(report: dict) -> str:
    """F_t as readable text."""
    lines = [f"analyst={report.get('analyst')} n_digests={report.get('n_digests')}"
             + (f" ERROR: {report['error']}" if report.get("error") else "")]
    for key, name in (("failure_modes", "mode"), ("capability_gaps", "gap"), ("success_habits", "habit")):
        items = report.get(key) or []
        lines.append(f"{key} ({len(items)}):")
        for it in items[:12]:
            if not isinstance(it, dict):
                continue
            aff = it.get("affected_tasks") or []
            lines.append(f"  - {it.get(name)} [n_tasks={it.get('n_tasks')}] {str(it.get('description', ''))[:300]}"
                         + (f" | tasks: {', '.join(map(str, aff[:8]))}" if aff else ""))
    return "\n".join(lines)


def strip_stable_prefix(prompt: str, stable: str) -> str:
    """The proposer prompt with the constant constitution prefix replaced by a marker (its full
    text is in the run_start event, identified by its sha)."""
    s = (stable or "").strip()
    if s and prompt.startswith(s):
        return f"[STABLE PREFIX: constitution SKILL.md + PATTERNS.md, {len(s)} chars, sha {sha(s)}; see run_start]" \
               + prompt[len(s):]
    return prompt


def gate_math(cand_ev: Measurement, inc_ev: Measurement, novelty: int, S_star: float, delta: float, cfg,
              gates, t: int) -> dict:
    """The numbers Algorithm 2 uses for one candidate, plus each gate's own verdict (recomputed
    with the same pure gate objects; checks stop at the first failure, as ``judge`` does)."""
    S, C, S_t, C_t = cand_ev.S, cand_ev.C, inc_ev.S, inc_ev.C
    dS = S - S_t
    dC = relative_cost_change(C, C_t)
    eps = float(getattr(cfg, "tie_eps", 0.0) or 0.0)          # 0.0: raw float comparisons, as in the code
    band = dS > delta + eps
    m = {"S_prime": S, "C_prime": C, "S_t": S_t, "C_t": C_t, "S_star": S_star, "delta": delta, "tie_eps": eps,
         "floor": S_star - delta, "above_floor": S >= S_star - delta - eps, "dS": dS, "dC": dC, "nu": novelty,
         "gain_above_band": band, "branch": "cost_rule" if band else "within_band_shaped",
         "cost_limit": cfg.beta0 + cfg.beta1 * dS, "shaped": cfg.w_s * dS - cfg.w_c * dC + cfg.w_n * novelty,
         "params": {"beta0": cfg.beta0, "beta1": cfg.beta1, "w_s": cfg.w_s, "w_c": cfg.w_c, "w_n": cfg.w_n}}
    checks = []
    c_sc, i_sc = cand_ev.as_scored(novelty), inc_ev.as_scored()
    ctx = GateContext(best_score=S_star, delta=delta, round=t)
    for g in gates:
        v = g.check(c_sc, i_sc, ctx)
        checks.append({"gate": g.name, "accept": bool(v.accept), "reason": v.reason,
                       "details": {k: (float(x) if isinstance(x, (int, float)) else x) for k, x in v.details.items()}})
        if not v.accept:
            break
    m["checks"] = checks
    return {k: (None if isinstance(v, float) and math.isnan(v) else v) for k, v in m.items()}


def _safe(fn):
    """Tracing must never change the run: swallow (and try to record) any tracing error."""
    @functools.wraps(fn)
    def wrapper(self, *a, **kw):
        if not self.enabled:
            return None
        try:
            return fn(self, *a, **kw)
        except Exception as e:  # noqa: BLE001
            try:
                t = next((x for x in a if isinstance(x, int) and not isinstance(x, bool)), kw.get("t"))
                self.tracer.event("note", t, stage="trace_error", where=fn.__name__, error=repr(e)[:500])
            except Exception:  # noqa: BLE001
                pass
            return None
    return wrapper


class RRSITrace:
    """RRSI-specific wrapper around :class:`rsi.trace.RunTracer` (no-op when disabled)."""

    def __init__(self, out_dir, *, enabled: bool = True, max_text: int = 100_000) -> None:
        self.tracer = RunTracer(out_dir if enabled else None, "rrsi", max_text=max_text)
        self.monitor_llm: Optional[LLM] = None

    @property
    def enabled(self) -> bool:
        return self.tracer.enabled

    def attach_monitor(self, domain, llm_task: Optional[LLM], *, k: int = 1, workers: int = 4) -> None:
        """ShadowMonitor on the sealed holdout / ood splits, with a separately metered task model."""
        if not self.enabled:
            return
        splits = [s for s in ("holdout", "ood") if s in domain.tasks.splits]
        if not splits:
            return
        self.monitor_llm = isolated_llm(llm_task)
        self.tracer.monitor = ShadowMonitor(sealed_view(domain, splits), self.monitor_llm, splits=splits, k=k,
                                            workers=workers)

    @_safe
    def event(self, kind: str, t: Optional[int] = None, **data: Any) -> None:
        self.tracer.event(kind, t, **data)

    @_safe
    def evaluation(self, t: Optional[int], candidate: str, ev: Measurement, *, kind: str = "eval",
                   truth: Optional[dict] = None, **extra: Any) -> None:
        data = {"candidate": candidate, **eval_payload(ev), **extra}
        if truth is not None:
            data["ground_truth"] = dict(truth, note="report-only analytic truth; never read by the loop")
        self.tracer.event(kind, t, **data)

    @_safe
    def kept(self, t: Optional[int], name: str, artifact, decision_score: Optional[float]) -> None:
        """A new incumbent: run the shadow monitor (write-only; errors are recorded, never raised)."""
        self.tracer.kept(t, name, artifact, decision_score)

    def monitor_usage(self) -> Optional[dict]:
        return None if self.monitor_llm is None else self.monitor_llm.meter.snapshot()

    # ================================================================== RRSI step events ==
    # Each method is wrapped by _safe: a tracing error is recorded as a note and never propagates.

    @staticmethod
    def _llms(r) -> list[LLM]:
        return r.llms()

    def spend(self, r) -> dict:
        tot = 0.0
        by: dict = {}
        for l in self._llms(r):
            for role, u in l.meter.by_role.items():
                by[role] = round(by.get(role, 0.0) + u.cost_usd, 6)
                tot += u.cost_usd
        mon = self.monitor_llm.meter.total().cost_usd if self.monitor_llm is not None else 0.0
        out = {"loop_usd": round(tot, 6), "by_role_usd": by, "shadow_monitor_usd": round(mon, 6)}
        led = getattr(r, "spend", None)
        if led is not None:          # budget view: earlier processes of this run + this one (rsi.rrsi.spend)
            out["budget_view"] = led.summary()
        return out

    def truth(self, r, artifact) -> Optional[dict]:
        fn = getattr(r.domain, "audit_truth", None)
        if fn is None or artifact is None:
            return None
        try:
            return fn(artifact)
        except Exception as e:  # noqa: BLE001
            return {"error": repr(e)[:300]}

    @_safe
    def run_start(self, r, budget=None, resumed: bool = False) -> None:
        from .schedule import budget_table
        cfg, sw = r.cfg, r.sw
        seed = r.seed_artifact
        self.tracer.event(
            "run_start", None, seed=seed.short_id, seed_artifact_id=seed.id,
            seed_files={p: len(txt) for p, txt in seed.files.items()}, resumed=resumed,
            settled_rounds=r.frontier.settled_rounds(), domain=getattr(r.domain, "name", "domain"),
            domain_description=r.domain.describe(),
            splits={s: len(ids) for s, ids in r.domain.tasks.splits.items()},
            config=cfg.dump(), switches=sw.to_json(), K=r.tax.K, K_str=r.tax.K_str,
            gates=[g.name for g in r.gates], guards=[g.name for g in r.guards],
            llms={"task": getattr(r.llm_task, "name", None), "proposer": getattr(r.llm_propose, "name", None),
                  "critic": getattr(r.llm_critic, "name", None) if r.critic is not None else None,
                  "analyst": getattr(r.llm_analyst, "name", None), "analyst_mode": r.analyst.mode,
                  "editor": type(r.editor).__name__ if r.editor is not None else None},
            b_t_schedule=budget_table(cfg.T, cfg.b_min, cfg.b_max, cfg.budget_rounding) if sw.budget_anneal
            else [sw.constant_budget or cfg.b_max] * cfg.T,
            budget={k: getattr(budget, k) for k in ("max_rounds", "max_rollouts", "max_usd", "max_wall_s")}
            if budget is not None else None,
            shadow_monitor={"splits": getattr(self.tracer.monitor, "splits", None),
                            "k": getattr(self.tracer.monitor, "k", None)} if self.tracer.monitor else None,
            proposer_system=r.proposer.system, proposer_system_sha=sha(r.proposer.system),
            constitution=r.proposer.stable, constitution_sha=sha(r.proposer.stable.strip()))

    @_safe
    def noise(self, cal: dict, fixed: Optional[float] = None) -> None:
        if fixed is not None:
            self.tracer.event("noise", None, delta=fixed, mode="fixed (Config.delta)", z=None,
                              detail="delta set in the config; no calibration")
            return
        self.tracer.event("noise", None, delta=cal.get("delta"), mode=cal.get("method"), z=cal.get("z"),
                          detail=(f"sd_null={cal.get('sd_null')} se_bootstrap={cal.get('se_bootstrap')} "
                                  f"sd_null_bootstrap={cal.get('sd_null_bootstrap')} n_evals={cal.get('n_evals')} "
                                  f"k={cal.get('k')} n_tasks={cal.get('n_tasks')}"
                                  + (f" WARNING: {cal['warning']}" if cal.get("warning") else "")),
                          calibration=cal)

    @_safe
    def round_start(self, r, t: int, fr: dict, delta: float, budget: int, sigma: int, explore: dict, prune: list,
                    tried: set) -> None:
        cfg, sw = r.cfg, r.sw
        traj = [x["S"] for x in fr["trajectory"]]
        stall = None
        if sw.stall_exploration:
            if t >= cfg.w and t < len(traj):
                stall = {"S_t": traj[t], "S_t_minus_w": traj[t - cfg.w], "diff": traj[t] - traj[t - cfg.w],
                         "w": cfg.w, "delta": delta, "rule": "sigma_t = 1[S_t - S_{t-w} <= delta]"}
            else:
                stall = {"rule": f"sigma_t = 0 while t < w = {cfg.w}"}
        g = r.history.yield_g(t, cfg.n_prune) if sw.prune_directives else {}
        recs = r.history.records()
        outcomes: dict = {}
        for x in recs:
            outcomes[x.get("outcome")] = outcomes.get(x.get("outcome"), 0) + 1
        untried = explore.get("untried") or []
        reserved = [chr(65 + v) for v in range(cfg.m) if sigma and untried and v >= cfg.m - cfg.m_draft]
        inc = fr["incumbent"]
        self.tracer.event(
            "round_start", t, t=t, T=cfg.T,
            incumbent={"node": inc.get("node"), "artifact": str(inc.get("artifact_id"))[:10], "S": inc.get("S"),
                       "C": inc.get("C"), "job": inc.get("job")},
            S_star=fr["S_star"], delta=delta, trajectory_S=traj,
            b_t=budget, b_t_inputs={"b_min": cfg.b_min, "b_max": cfg.b_max, "T": cfg.T,
                                    "rounding": cfg.budget_rounding, "anneal": sw.budget_anneal},
            sigma_t=sigma, stall=stall, tried_T_t=sorted(tried), untried_U_t=untried, m=cfg.m, m_draft=cfg.m_draft,
            reserved_variants=reserved,
            prune_B_t=[{"component": p["component"], "recent_best_gain": p.get("recent_best_gain"),
                        "n_accepted_edits_in_incumbent": len(p.get("accepted_edits_in_incumbent") or [])}
                       for p in prune],
            yield_g_t={c: (None if v == -math.inf else round(v, 6)) for c, v in g.items()},
            memory={"history_records": len(recs), "history_outcomes": outcomes,
                    "measured_edits": len(r.history.measured()), "scoreboard_rows": len(r.scoreboard.rows()),
                    "accepted_edits_per_component": r.history.incumbent_component_counts(before_t=t)},
            n_rollouts=r.measurer.n_rollouts, spend=self.spend(r))

    @_safe
    def analysis(self, r, t: int, report: dict, digests: list, traces: dict, inc_ev: Measurement,
                 reused: bool) -> None:
        means = inc_ev.task_means()
        self.tracer.event(
            "analysis", t, text=report_text(report), reused_from_disk=reused, analyst_mode=r.analyst.mode,
            traces_read={"fail": {tid: round(means.get(tid, 0.0), 3) for tid, x in traces.items()
                                  if x.get("_role") == "fail"},
                         "win": {tid: round(means.get(tid, 0.0), 3) for tid, x in traces.items()
                                 if x.get("_role") != "fail"}},
            n_digests=len(digests or []), report=report,
            llm_calls=[] if reused else [dict(c, system_sha=sha(c.get("system", "")), system=None)
                                         for c in getattr(r.analyst, "last_calls", [])])

    @_safe
    def proposal_turns(self, r, t: int, vid: str, parent: str, inc_art, prop: dict, attempt: int,
                       reserved: bool, budget: int) -> None:
        for turn in prop.get("turns") or []:
            art = turn.get("artifact")
            diff = inc_art.diff(art) if art is not None else ""
            dec = turn.get("declared_edits") or []
            self.tracer.proposal(
                t, f"r{t}{vid}", parent=parent, prompt=strip_stable_prefix(turn.get("prompt") or "", r.proposer.stable),
                reply=turn.get("reply") or "", change=str(turn.get("summary") or ""),
                hypothesis=" | ".join(str(e.get("hypothesis")) for e in dec),
                components=[str(e.get("component")) for e in dec], diff=diff, error=turn.get("error"),
                attempt=attempt, call_kind="initial" if attempt == 0 else f"critic repair {attempt}",
                turn=turn.get("turn"), outcome=turn.get("outcome"), n_changes=turn.get("n_changes"),
                changed_files=inc_art.changed_files(art) if art is not None else [],
                declared_edits=dec, reserved_slot=reserved, b_t=budget, blocked=turn.get("blocked"),
                artifact=getattr(art, "short_id", None), status_of_call=prop.get("status"))

    @_safe
    def critic(self, r, t: int, vid: str, attempt: int, verdict: dict, exchange: Optional[dict],
               reserved_override: bool) -> None:
        self.tracer.event("critic", t, candidate=f"r{t}{vid}", attempt=attempt,
                          accept=verdict.get("verdict") == "accept", stage=verdict.get("stage"),
                          objections=verdict.get("reasons") or [], risk_notes=verdict.get("risk_notes") or [],
                          reserved_slot_override=reserved_override, llm=exchange or None)

    @_safe
    def note(self, t: Optional[int], **data: Any) -> None:
        self.tracer.event("note", t, **data)

    @_safe
    def tagging(self, r, t: int, vid: str, edits: list, diff: str) -> None:
        from .components import changed_paths
        rows = [{"id": e.get("id"), "declared": e.get("declared_component"), "normalized": e.get("component"),
                 "retagged": e.get("declared_component") != e.get("component")} for e in edits]
        self.tracer.event("note", t, stage="tagging", candidate=f"r{t}{vid}", files_in_diff=changed_paths(diff),
                          edits=rows, diff_lines=sum(1 for l in diff.splitlines() if l[:1] in "+-"))

    @_safe
    def evaluations(self, r, t: int, cands: list, inc_ev: Measurement) -> None:
        for c in cands:
            if c.ev is not None:
                self.tracer.event("eval", t, candidate=f"r{t}{c.variant}", **eval_payload(c.ev),
                                  vs_incumbent=per_task_change(inc_ev, c.ev),
                                  **({"ground_truth": self.truth(r, r.store.get(c.artifact_id))}
                                     if getattr(r.domain, "audit_truth", None) else {}))
            elif c.gate_failure == "eval_invalid":
                self.tracer.event("note", t, stage="eval_invalid", candidate=f"r{t}{c.variant}", detail=c.detail)

    @_safe
    def selection(self, r, t: int, cands: list, decisions: list, winner, inc_ev: Measurement, S_star: float,
                  delta: float, inc_node: str, S_star_after: float, new_node: str) -> None:
        for c, dec in zip(cands, decisions):
            name = f"r{t}{c.variant}"
            if c.ev is None:
                self.tracer.gate(t, name, False, dec.reason,
                                 {"gate_failure": c.gate_failure, "detail": (c.detail or "")[:600]},
                                 stage="gate_failure (never evaluated)")
                continue
            m = gate_math(c.ev, inc_ev, dec.novelty, S_star, delta, r.cfg, r.gates, t)
            recomputed = all(x["accept"] for x in m["checks"])
            self.tracer.gate(t, name, dec.admissible, dec.reason, m, stage="Algorithm 2",
                             recomputed_admissible=recomputed, consistent=recomputed == dec.admissible)
        adm = {f"r{t}{c.variant}": c.ev.S for c, d in zip(cands, decisions) if d.admissible and c.ev is not None}
        if winner is not None:
            why = (f"argmax S' over the admissible set {adm} -> r{t}{winner.variant} (S'={winner.ev.S:.4f}); "
                   f"S* {S_star:.4f} -> {S_star_after:.4f}")
        else:
            why = "no admissible candidate -> H_{t+1} = H_t; " + "; ".join(
                f"r{t}{c.variant}: {d.reason[:160]}" for c, d in zip(cands, decisions))
        self.tracer.decision(t, kept=f"r{t}{winner.variant}" if winner is not None else None,
                             incumbent_before=inc_node, incumbent_after=new_node, why=why, admissible=adm,
                             S_star_before=S_star, S_star_after=S_star_after,
                             S_incumbent_before=inc_ev.S, S_incumbent_after=winner.ev.S if winner else inc_ev.S)

    @_safe
    def state(self, r, t: int, fr: dict) -> None:
        inc = fr["incumbent"]
        art = r.store.get(inc["artifact_id"])
        rows = [x for x in r.scoreboard.rows() if x.get("t") == t]
        self.tracer.event(
            "state", t, incumbent={"node": inc.get("node"), "artifact": str(inc["artifact_id"])[:10],
                                   "S": inc.get("S"), "C": inc.get("C")},
            S_star=fr["S_star"], trajectory=[{"t": x["t"], "S": round(x["S"], 4), "node": x.get("node")}
                                             for x in fr["trajectory"]],
            harness_files={p: len(txt) for p, txt in art.files.items()},
            tried=sorted(r.history.tried()), accepted_edits_per_component=r.history.incumbent_component_counts(),
            history_records=len(r.history.records()),
            scoreboard_this_round=[{k: x.get(k) for k in ("variant", "edit_id", "component", "n_predicted",
                                                           "predicted_hit", "hit_rate", "unpredicted_regressions")}
                                   for x in rows],
            n_rollouts=r.measurer.n_rollouts, spend=self.spend(r))

    @_safe
    def run_end(self, r, stop: str, wall_s: float) -> None:
        from .driver import merged_usage
        fr = r.frontier.load() if r.frontier.exists() else {}
        inc = fr.get("incumbent") or {}
        self.tracer.event(
            "run_end", None, stop_reason=stop, rounds_settled=r.frontier.settled_rounds(),
            final_incumbent={"node": inc.get("node"), "artifact": str(inc.get("artifact_id"))[:10],
                             "S": inc.get("S"), "C": inc.get("C")},
            S_star=fr.get("S_star"), trajectory_S=[x["S"] for x in fr.get("trajectory", [])],
            usage=merged_usage(self._llms(r)), shadow_monitor_usage=self.monitor_usage(), spend=self.spend(r),
            n_rollouts=r.measurer.n_rollouts, wall_s=round(wall_s, 1),
            critic=None if r.critic is None else {"reviews": r.critic.n_reviews,
                                                  "precheck_rejects": r.critic.n_precheck_rejects,
                                                  "llm_rejects": r.critic.n_llm_rejects})
