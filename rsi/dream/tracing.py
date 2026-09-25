"""Per-iteration audit trace of a Dream-RSI run (``rsi.trace`` format) and a write-only shadow monitor.

:class:`DreamTracer` maps what the loop does onto the uniform :mod:`rsi.trace` event kinds,
written to ``<out_dir>/trace.jsonl`` (render with ``rsi.trace.inspect(out_dir)``). One trace
"round" is one live cycle ``t`` (online search + dreaming phase):

``run_start``   config, seed artifact id, task, initial policy, agent / developer / LLMs, objective
``baseline``    the seed program's locked evaluation (per task + raw trial scores)
``noise``       argmax selection has no noise band (paper §3); the guarded selector's margin is per selection
``round_start`` loop state before the live search: deployed policy (id, label, baked-in beta), the plan
                it asked for and the validated plan, directions per root, the live root program, best
                so far, world pool / manifests / version history sizes, calls used and left, spend
``note``        ``online_round``: every online decision round (prefix summary the policy saw, legal-set
                size, the selected batch, revealed outcomes); ``online_summary``; ``live_cycle_manifest``;
                ``beta_sweep`` of the deployed version
``proposal``    object level: every discovery-agent attempt (prompt, raw reply, claimed change /
                hypothesis, the ACTUAL program diff vs the parent workspace); policy level: every
                developer revision (prompt + reply or the mutator's feedback-directed moves, the actual
                ``method.py`` diff vs the version it started from)
``critic``      the policy screen: static check + leakage screen (LLM path, incl. repair rounds)
``eval``        each attempt's locked evaluation; each policy version's replay report (V, per-world
                V_i, and per world: plan, N, k, batch sizes, best, the Eq.-1 terms, the reveal batches)
``gate``        object level: the round's best attempt vs the best so far (strict >); policy level:
                each version's admissibility and V^m vs V^0 with the selection arithmetic
``decision``    object level: best program before/after; policy level: deployed policy before/after
``monitor``     :class:`rsi.trace.ShadowMonitor` sealed-split scores of every new best program
``state``       loop state after the cycle (the trajectory row, meters)
``run_end``     stop reason, seed / best, totals, spend (loop and monitor)

Everything here only *reads* loop state; no RNG is drawn and nothing is returned to the loop.
The shadow monitor's model calls go through :class:`ShadowLLM` (roles ``shadow:*``), so the loop's
usage never includes them.
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict
from typing import Any, Optional, Sequence

from ..core.artifact import Artifact
from ..core.llm import LLM, LLMResponse
from ..trace import RunTracer
from .policy import POLICY_FILE

SHADOW_PREFIX = "shadow:"


class ShadowLLM(LLM):
    """The frozen task LLM as seen by the shadow monitor: same model, spend metered separately."""

    def __init__(self, inner: LLM) -> None:
        super().__init__()
        self.inner = inner
        self.name = inner.name

    def complete(self, prompt, *, system=None, max_tokens=None, seed=None, role="default") -> LLMResponse:
        resp = self.inner.complete(prompt, system=system, max_tokens=max_tokens, seed=seed, role=SHADOW_PREFIX + role)
        self.meter.add(role, resp.usage)
        return resp


def sealed_splits(domain) -> list[str]:
    ts = getattr(domain, "tasks", None)
    if ts is None:
        return []
    return [s for s in ("holdout", "ood") if s in ts.splits and ts.is_sealed(s) and ts.splits[s]]


def _r(x: Any, n: int = 6) -> Any:
    if isinstance(x, float):
        return x if not math.isfinite(x) else round(x, n)
    return x


def _code_diff(old: str, new: str) -> str:
    return Artifact({POLICY_FILE: old or ""}).diff(Artifact({POLICY_FILE: new or ""}))


def _name(obj) -> Optional[str]:
    if obj is None:
        return None
    return getattr(obj, "name", None) or type(obj).__name__


class DreamTracer:
    """Writes the audit trace of one :class:`~rsi.dream.loop.DreamRSILoop` run."""

    def __init__(self, loop, tracer: RunTracer) -> None:
        self.loop = loop
        self.tr = tracer

    @property
    def enabled(self) -> bool:
        return self.tr.enabled

    # ---------------------------------------------------------------- helpers
    def _task_ids(self) -> list[str]:
        dom = getattr(self.loop.task, "domain", None)
        split = getattr(self.loop.task, "split", "evolve")
        try:
            return list(dom.tasks.splits.get(split, [])) if dom is not None else []
        except Exception:  # noqa: BLE001
            return []

    def eval_payload(self, outcome, artifact: Optional[Artifact]) -> dict:
        """summary + per_task + raw trial scores of one locked evaluation (object level)."""
        task = self.loop.task
        split = getattr(task, "split", "evolve")
        summary = {"split": split, "S": outcome.score, "fail_class": outcome.fail_class, "valid": outcome.valid,
                   "evaluated": outcome.evaluated, "errors": 0 if outcome.fail_class == "ok" else 1,
                   "missing": 0, "k": getattr(task, "k", 1), "seconds": _r(outcome.seconds, 3)}
        res = getattr(task, "last_results", {}).get(artifact.id) if artifact is not None else None
        if res is not None:
            per_task = res.task_scores()
            trials = {tid: [t.score for t in trs] for tid, trs in res.trials.items()}
            summary.update(n_tasks=len(per_task), C=res.cost, errors=res.error_rate)
        else:
            ids = self._task_ids() or [getattr(task, "name", "task")]
            per_task = {tid: outcome.score for tid in ids}
            trials = {tid: [outcome.score] for tid in ids}
            summary.update(n_tasks=len(ids))
        diag = {k: v for k, v in (outcome.diagnostics or {}).items() if k != "feedback"}
        return {"summary": summary, "per_task": per_task, "trials": trials, "error": outcome.error,
                "diagnostics": diag}

    def _policy_desc(self, rec) -> dict:
        from .developer import default_beta_of

        return {"label": rec.label, "rev": f"r{rec.index:04d}", "id": Artifact({POLICY_FILE: rec.code}).short_id,
                "default_beta": default_beta_of(rec.code)}

    # ----------------------------------------------------------------- events
    def run_start(self, seed_prog: Artifact) -> None:
        lp, c = self.loop, self.loop.cfg
        mon = self.tr.monitor
        self.tr.event("run_start", None, seed=seed_prog.short_id, seed_artifact_id=seed_prog.id,
                      method=lp.method, task=getattr(lp.task, "name", None), problem=lp.task.describe()[:1500],
                      config=asdict(c), n_revisions_per_phase=c.n_revisions,
                      initial_policy={"id": Artifact({POLICY_FILE: lp.initial_code}).short_id,
                                      "is_parallel_refine": lp.initial_code == lp.baseline_code},
                      directions=list(lp.task.directions()), editable=lp.task.editable(),
                      agent=_name(lp.agent), developer=_name(lp.developer),
                      llms=[_name(x) for x in lp.llms], objective=lp.replay.objective.describe(),
                      selector=lp.selector.name, sandbox=c.sandbox,
                      shadow_monitor={"splits": mon.splits, "k": mon.k} if mon is not None else None)

    def noise(self) -> None:
        c = self.loop.cfg
        if c.selector == "guarded":
            self.tr.event("noise", None, delta=None, mode="per-selection paired margin on held-out replay worlds",
                          z=c.noise_z, detail=f"delta = max({c.margin_floor}, z * se(held-out paired diffs)); "
                                              "computed at every selection (see the gate arithmetic)")
        else:
            self.tr.event("noise", None, delta=None, mode="none", z=None,
                          detail="paper rule: argmax of the mean replay score over ALL worlds, incumbent included "
                                 "(replay is deterministic, so a version's V has no sampling noise; the online "
                                 "search itself is stochastic and has no noise band in the paper)")

    def baseline(self, outcome, prog: Artifact) -> None:
        self.tr.event("baseline", None, candidate="seed", artifact=prog.short_id, **self.eval_payload(outcome, prog))

    def round_start(self, t: int, policy, req, plan, perr, pnote, directions: dict, root_art: Artifact,
                    root_score, best_score, calls_left) -> None:
        lp = self.loop
        m = lp.meter
        self.tr.event("round_start", t, live_cycle=t, deployed_policy=self._policy_desc(policy),
                      plan_requested=None if req is None else req.to_dict(), plan_used=plan.to_dict(),
                      plan_error=perr, plan_note=pnote, W=lp.cfg.W, max_calls_this_round=calls_left,
                      directions={str(b): d.get("direction") for b, d in directions.items()},
                      live_root={"artifact": root_art.short_id, "score": root_score, "mode": lp.cfg.root},
                      best_so_far=_r(best_score), n_worlds=len(lp.worlds), n_manifests=len(lp.manifests),
                      n_versions_evaluated=len(lp.history), rev_counter=lp.rev_counter,
                      agent_calls_used=m.agent_calls, developer_calls_used=m.developer_calls,
                      replay_episodes_so_far=m.replay_episodes, usd_so_far=_r(m.agent_usd + m.developer_usd),
                      guidance=(lp.guidance.text[:600] if (lp.cfg.guidance and lp.guidance) else None))

    def online(self, t: int, q, out) -> None:
        """Every online decision round, then every attempt (proposal + eval), in creation order."""
        tree = q.tree
        for row in q.round_log:
            self.tr.event("note", t, what="online_round", online_round=row["round"], prefix_seen=row["prefix"],
                          legal_n=row["legal_n"], batch=row["batch"],
                          revealed=[{"cell": x["cell"], "score": _r(x["score"]), "fail_class": x["fail_class"]}
                                    for x in row["revealed"]], empty=row["empty"])
        log = q.attempt_log or {}
        for n in tree.non_root():
            rec = log.get(n.id, {})
            parent_prog, prog = rec.get("parent_program"), rec.get("program")
            diff = parent_prog.diff(prog) if (parent_prog is not None and prog is not None) else ""
            parent = tree.node(n.parent_id)
            self.tr.proposal(t, f"t{t}/{n.id}", parent=f"t{t}/{n.parent_id}", prompt=rec.get("prompt", ""),
                             reply=rec.get("reply", ""), change=rec.get("change") or
                             (rec.get("proposal", "").splitlines() or [""])[0], hypothesis=rec.get("hypothesis", ""),
                             components=rec.get("components", []), diff=diff, error=rec.get("agent_error"),
                             branch=n.branch, attempt=n.attempt, online_round=n.round,
                             direction=(n.tags or {}).get("direction"), context=rec.get("context"),
                             blocked_files=rec.get("blocked", []), usage=rec.get("usage"),
                             diff_empty=bool(prog is not None and parent_prog is not None and prog.id == parent_prog.id))
            from .agent import EvalOutcome

            ev = EvalOutcome(n.score, n.evaluated, n.valid, n.fail_class, n.error, n.n_valid, n.n_total,
                             dict(n.diagnostics or {}), float(rec.get("eval_s") or 0.0))
            pay = self.eval_payload(ev, prog)
            ps = parent.score if parent.success else None
            self.tr.event("eval", t, candidate=f"t{t}/{n.id}", **pay, success=n.success,
                          delta_vs_parent=_r(n.score - ps) if (n.success and ps is not None and n.score is not None)
                          else None,
                          delta_vs_root=_r(n.score - tree.root_score) if (n.success and n.score is not None) else None)
        self.tr.event("note", t, what="online_summary", N=q.N, k=q.k, batch_sizes=list(q.batch_sizes),
                      agent_calls=q.calls, online_error=out.error, violations=out.violations,
                      truncated_batch=tree.meta.get("truncated_batch"),
                      grid=tree.render_grid(q.reveal_round) if hasattr(tree, "render_grid") else None)

    def best_program(self, t: int, tree, before: float, before_label: str, after: float, after_label: str,
                     best_art: Artifact) -> None:
        succ = [n for n in tree.non_root() if n.success and n.score is not None]
        top = max(succ, key=lambda n: (n.score, -n.seq)) if succ else None
        acc = top is not None and top.score > before
        self.tr.gate(t, f"t{t}/{top.id}" if top else "(no successful attempt)", acc,
                     "round's best successful attempt replaces the best program iff strictly greater"
                     if top else "no successful attempt this round",
                     math={"round_best": _r(top.score) if top else None, "best_before": _r(before),
                           "rule": "round_best > best_before", "delta": _r(top.score - before) if top else None},
                     level="object (best program)")
        self.tr.decision(t, kept=after_label if acc else None, incumbent_before=before_label,
                         incumbent_after=after_label, level="object (best program)",
                         why=(f"best program {before:.6g} -> {after:.6g}" if acc else
                              f"best program unchanged at {before:.6g}"), best_before=_r(before), best_after=_r(after))
        if acc:
            self.tr.kept(t, after_label, best_art, after)

    def manifest(self, t: int, man: dict) -> None:
        self.tr.event("note", t, what="live_cycle_manifest", manifest=man)

    # ------------------------------------------------------------- dreaming
    def replay_eval(self, t: int, rec, rep, *, role: str) -> None:
        obj = self.loop.replay.objective
        worlds = []
        for i, ep in enumerate(rep.episodes):
            w = {"world": ep.world_id, "plan": ep.plan, "requested_plan": ep.requested_plan,
                 "out_of_support": ep.out_of_support, "N": ep.N, "k": ep.k, "batch_sizes": ep.batch_sizes,
                 "best": _r(ep.best), "root": _r(ep.root), "ceiling": _r(ep.ceiling), "world_size": ep.world_size,
                 "attainment": _r(ep.attainment), "disqualified": ep.disqualified,
                 "V_i": _r(rep.per_world[i]) if i < len(rep.per_world) else None,
                 "reveal_batches": [{"r": row["round"], "batch": row["batch"],
                                     "revealed": [f"{x['cell']}={_r(x['score'], 4)}" if x["fail_class"] == "ok"
                                                  else f"{x['cell']}:{x['fail_class']}" for x in row["revealed"]],
                                     "empty": row["empty"]} for row in ep.trace]}
            if hasattr(obj, "quality") and not ep.disqualified:
                w["terms"] = {"quality": _r(obj.quality(ep)), "cost": _r(-obj.beta1 * ep.N),
                              "parallel_bonus": _r(obj.beta2 * ep.N / max(1, ep.k))}
            if ep.error or ep.violations or ep.batch_errors:
                w["problems"] = {"error": str(ep.error)[-400:] if ep.error else None, "violations": ep.violations,
                                 "batch_errors": ep.batch_errors}
            worlds.append(w)
        self.tr.event("eval", t, candidate=f"r{rec.index:04d}_{rec.label}", role=role,
                      summary={"split": f"replay worlds H_{t} ({len(rep.per_world)})", "S": _r(rep.value),
                               "objective": rep.objective, "n_tasks": len(rep.per_world), "k": 1,
                               "errors": rep.disqualified, "missing": 0},
                      per_task={w["world"]: w["V_i"] for w in worlds},
                      trials={w["world"]: [w["V_i"]] for w in worlds}, worlds=worlds,
                      diagnostics=rep.diagnostics, sweep_reward=None if rep.sweep is None else _r(rep.sweep["reward"]),
                      replay_cpu_s=_r(rep.cpu_s, 4), n_episodes=rep.n_episodes)

    def analysis(self, t: int, inc, dev_idx: Sequence[int], forbidden: Sequence[str]) -> None:
        rep = inc.report
        lines = [f"Dreaming phase after live cycle {t}: {len(self.loop.worlds)} replay worlds, development worlds "
                 f"{list(dev_idx)}.",
                 f"Incumbent {inc.label} (r{inc.index:04d}) replay value V^0 = {rep.value:.6f}; per world "
                 f"{[round(v, 4) for v in rep.per_world]}.",
                 "Feedback diagnostics the developer acts on: " + json.dumps(rep.diagnostics, default=str),
                 f"Leakage screen terms (best cell ids / scores of every world): {list(forbidden)[:12]}"]
        self.tr.event("analysis", t, text="\n".join(lines), incumbent=f"r{inc.index:04d}",
                      diagnostics=rep.diagnostics)

    def revision(self, t: int, rec, rev, base_code: str, screen_ok: bool, screen_errors: list[str]) -> None:
        meta = rev.meta or {}
        calls = meta.get("calls") or []
        last = calls[-1] if calls else {}
        if calls:
            self._save_prompts(rec, calls)
        diff = _code_diff(base_code, rev.code) if rev.code else ""
        self.tr.proposal(t, f"r{rec.index:04d}_{rec.label}",
                         parent=f"r{rev.parent:04d}" if rev.parent is not None else None,
                         prompt=last.get("prompt", ""), reply=last.get("reply", ""), change=rev.change,
                         hypothesis=meta.get("hypothesis", "") or last.get("hypothesis", ""), components=["method.py"],
                         diff=diff, error=rev.error, developer=_name(self.loop.developer),
                         moves=meta.get("moves"), params_changed=meta.get("params_changed"),
                         feedback_used=meta.get("feedback"), base_value=_r(meta.get("base_value")) if
                         isinstance(meta.get("base_value"), float) else meta.get("base_value"),
                         llm_calls=len(calls), repairs=meta.get("repairs"),
                         context_files=meta.get("context_files"), diff_empty=bool(rev.code) and not diff)
        for c in calls:
            sc = c.get("screen") or {}
            self.tr.event("critic", t, candidate=f"r{rec.index:04d}_{rec.label}", accept=bool(sc.get("ok")),
                          stage=f"developer attempt {c.get('attempt')}: static check + leakage screen",
                          objections=list(sc.get("errors", [])) + [f"leak: {h}" for h in sc.get("leak_hits", [])])
        self.tr.event("critic", t, candidate=f"r{rec.index:04d}_{rec.label}", accept=bool(screen_ok),
                      stage="loop: revision ok + static_check before replay", objections=screen_errors)

    def _save_prompts(self, rec, calls) -> None:
        out = self.loop.out
        if out is None:
            return
        d = out / "dream_prompts"
        d.mkdir(parents=True, exist_ok=True)
        for c in calls:
            stem = f"r{rec.index:04d}_{rec.label}_a{c.get('attempt', 0)}"
            (d / f"{stem}_prompt.txt").write_text(str(c.get("prompt", "")))
            (d / f"{stem}_reply.txt").write_text(str(c.get("reply", "")))

    def selection(self, t: int, versions, reports, sel, incumbent) -> None:
        v0 = reports[0].value
        for i, (v, r) in enumerate(zip(versions, reports)):
            finite = math.isfinite(r.value)
            self.tr.gate(t, f"r{v.index:04d}_{v.label}", finite and i == sel.index,
                         ("selected: " if i == sel.index else "not selected: ") + sel.reason
                         + ("" if finite else " (not admissible: no replay value)"),
                         math={"V_m": _r(r.value), "V_0_incumbent": _r(v0), "delta_vs_incumbent":
                               _r(r.value - v0) if finite else None, "all_V": [_r(x) for x in sel.values],
                               "argmax_index": sel.index, "per_world": [_r(x) for x in r.per_world],
                               "beta1": getattr(self.loop.replay.objective, "beta1", None),
                               "beta2": getattr(self.loop.replay.objective, "beta2", None),
                               "normalized": getattr(self.loop.replay.objective, "normalize", None),
                               "selector_details": json.loads(json.dumps(sel.details, default=float))},
                         level="policy (replay selection)", index=i)
        chosen = versions[sel.index]
        self.tr.decision(t, kept=f"r{chosen.index:04d}_{chosen.label}" if sel.index != 0 else None,
                         incumbent_before=f"r{incumbent.index:04d}_{incumbent.label}",
                         incumbent_after=f"r{chosen.index:04d}_{chosen.label}", level="policy (replay selection)",
                         why=f"{sel.reason}; V = {[round(x, 6) if math.isfinite(x) else x for x in sel.values]}",
                         deployed_for_next_cycle=self._policy_desc(chosen))

    def sweep(self, t: int, chosen, sweep: Optional[dict]) -> None:
        if not sweep:
            return
        self.tr.event("note", t, what="beta_sweep", policy=f"r{chosen.index:04d}_{chosen.label}",
                      reward=_r(sweep.get("reward")), auc=_r(sweep.get("auc")),
                      parallel_penalty=_r(sweep.get("parallel_penalty")), degenerate=sweep.get("degenerate"),
                      points=[{k: _r(v, 4) for k, v in p.items()} for p in sweep.get("points", [])])

    def state(self, t: int, row: dict, policy, best_art: Artifact, best_score: float) -> None:
        lp = self.loop
        self.tr.event("state", t, deployed_policy=self._policy_desc(policy), best_score=_r(best_score),
                      best_artifact=best_art.short_id, n_worlds=len(lp.worlds),
                      world_sizes=[w.size for w in lp.worlds], cost=lp.meter.snapshot(),
                      trajectory_row={k: v for k, v in row.items() if k != "dream"},
                      dream=(row.get("dream") or {}).get("values"))

    def run_end(self, res, shadow_llm: Optional[LLM]) -> None:
        lp = self.loop
        self.tr.event("run_end", None, stop_reason=res.stop_reason, seed_score=_r(res.meta["seed_score"]),
                      best_score=_r(res.meta["best_score"]), best_artifact=res.best.short_id,
                      final_policy=Artifact({POLICY_FILE: res.meta["policy"]}).short_id,
                      calls_per_cycle=[r["calls"] for r in res.trajectory], cost=lp.meter.snapshot(),
                      loop_usage={k: v for k, v in res.usage.items() if not k.startswith("_")},
                      shadow_usage=None if shadow_llm is None else shadow_llm.meter.snapshot())
