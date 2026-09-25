"""Per-iteration audit trace of a GEPA run (``rsi.trace`` format) and a write-only shadow monitor.

:class:`GEPATracer` turns what the engine does into the uniform :mod:`rsi.trace` event
kinds, written to ``<out_dir>/trace.jsonl`` (render with ``rsi.trace.inspect(out_dir)``):

``run_start``   config, seed artifact id, components, D_train / D_pareto sizes, stoppers
``baseline``    the seed on D_pareto (per task + raw trial scores)
``noise``       GEPA has no noise band (strict minibatch comparison, N = 1); recorded as such
``round_start`` loop state: rollouts used / budget / by phase, pool (val mean, frontier wins,
                round-robin pointer), best, Pareto sampling weights after set-cover pruning,
                batch-sampler epoch, merge schedule, spend
``note``        parent selection (selector, weights, chosen parent, minibatch ids) and merge attempts
``eval``        every evaluation: parent / child minibatch, full D_pareto, merge subsample
``analysis``    the parent's minibatch diagnosis the reflection LM is shown (score + feedback per record)
``proposal``    reflection prompt + raw reply, the reflection LM's commentary, components and the ACTUAL
                diff parent -> child (merges: sources per module, diffs vs both parents and the ancestor)
``critic``      the optional pre-evaluation screen
``gate``        minibatch acceptance with the numbers (before/after scores, sums, delta, rule); merge
                acceptance (subsample sum vs the better parent's)
``decision``    added to the pool as ``c<idx>`` or not, incumbent (argmax mean D_pareto) before/after,
                frontier delta, why
``monitor``     :class:`rsi.trace.ShadowMonitor` scores of every new incumbent on sealed splits
``state``       loop state after the iteration
``run_end``     stop reason, totals, budget identity, loop spend and monitor spend

Everything here only *reads* engine state; no RNG is drawn and nothing is returned to the loop.
The shadow monitor's model calls go through :class:`ShadowLLM`, which files them under roles
``shadow:*`` that the engine excludes from its own spend (so a USD stopper never sees them).
"""
from __future__ import annotations

from typing import Any, Optional, Sequence

from ..core.artifact import Artifact
from ..core.llm import LLM, LLMResponse
from ..trace import RunTracer, ShadowMonitor
from .frontier import pareto_frequencies

SHADOW_PREFIX = "shadow:"


class ShadowLLM(LLM):
    """The task LLM as seen by the shadow monitor: calls are delegated unchanged but metered
    under ``shadow:<role>`` so the loop's usage, USD budget and reflection-cost stoppers never
    include the monitor's spend."""

    def __init__(self, inner: LLM) -> None:
        super().__init__()
        self.inner = inner
        self.name = inner.name          # same model: same cache identities

    def complete(self, prompt, *, system=None, max_tokens=None, seed=None, role="default") -> LLMResponse:
        resp = self.inner.complete(prompt, system=system, max_tokens=max_tokens, seed=seed,
                                   role=SHADOW_PREFIX + role)
        self.meter.add(role, resp.usage)
        return resp


def default_shadow_splits(domain) -> list[str]:
    """Sealed holdout/ood splits of ``domain``; if it has none, its sealed ``test`` split."""
    ts = domain.tasks
    out = [s for s in ("holdout", "ood") if s in ts.splits and ts.is_sealed(s) and ts.splits[s]]
    if not out and "test" in ts.splits and ts.is_sealed("test") and ts.splits["test"]:
        out = ["test"]
    return out


def _clip(s: Any, n: int) -> str:
    s = str(s)
    return s if len(s) <= n else s[:n] + f"...[+{len(s) - n} chars]"


def _clip_mid(s: Any, head: int = 100, tail: int = 260) -> str:
    """Head and tail of a long output (the final answer usually sits at the end)."""
    s = str(s)
    return s if len(s) <= head + tail + 20 else s[:head] + f" ...[{len(s) - head - tail} chars]... " + s[-tail:]


def fenced_commentary(reply: str) -> str:
    """The reflection LM's text *outside* the fenced block it returned (its stated reasoning)."""
    r = (reply or "").strip()
    a, b = r.find("```"), r.rfind("```")
    if a == -1 or b <= a:
        return ""
    return (r[:a] + "\n" + r[b + 3:]).strip()


class GEPATracer:
    """Writes the audit trace of one :class:`~rsi.gepa.engine.GEPAEngine` run."""

    def __init__(self, engine, tracer: RunTracer) -> None:
        self.eng = engine
        self.tr = tracer

    @property
    def enabled(self) -> bool:
        return self.tr.enabled

    # ------------------------------------------------------------------ helpers --
    def label(self, idx: Optional[int]) -> Optional[str]:
        return None if idx is None else f"c{idx}"

    def state_snapshot(self) -> dict:
        eng, st = self.eng, self.eng.state
        cfg = eng.cfg
        agg = st.agg_scores()
        front = st.frontier.mapping()
        wins = {k: sum(1 for v in front.values() if k in v) for k in range(len(st.candidates))}
        try:
            weights = pareto_frequencies(front, agg) if front else {}
        except AssertionError:          # never expected; the trace must not break the run
            weights = {}
        b = st.best_idx() if st.candidates else None
        used = st.counter.total
        snap = {
            "iteration": st.i,
            "rollouts_used": used,
            "rollout_budget": cfg.max_metric_calls,
            "rollouts_remaining": (cfg.max_metric_calls - used) if cfg.max_metric_calls is not None else None,
            "rollouts_by_phase": {k: v for k, v in st.counter.by_phase.items() if v},
            "n_candidates": len(st.candidates),
            "incumbent": self.label(b), "incumbent_val": round(agg[b], 4) if b is not None else None,
            "pareto_sampling_weights": {f"c{k}": f for k, f in sorted(weights.items())},
            "frontier_keys": len(front),
            "frontier_members": [f"c{k}" for k in st.frontier.members()],
            "sampler": {"epoch": eng.sampler.epoch, "epoch_len": len(eng.sampler.shuffled_ids)},
            "n_proposals": st.n_proposals, "n_reflection_calls": st.n_reflection_calls,
            "loop_usd": round(eng.usd(), 6),
        }
        if eng.merge is not None:
            m = eng.merge
            snap["merge"] = {"merges_due": m.merges_due, "total_merges_tested": m.total_merges_tested,
                             "n_invocations": m.n_invocations,
                             "last_iter_found_new_program": m.last_iter_found_new_program,
                             "cap": m.max_merge_invocations, "cap_mode": m.cap_mode}
        # pool last: long, and the rendered TRACE.md clips this event
        snap["pool"] = [{"c": k, "parents": st.parents[k], "kind": st.kinds[k], "val_mean": round(agg[k], 4),
                         "frontier_wins": wins[k], "next_component": st.components[st.rr[k]],
                         "found_at_iteration": st.discovery_iter[k]} for k in range(len(st.candidates))]
        return snap

    # ------------------------------------------------------------------- events --
    def run_start(self, resumed_at: Optional[int]) -> None:
        eng = self.eng
        mon = self.tr.monitor
        self.tr.event("run_start", None, seed=eng.seed_artifact.short_id, seed_artifact_id=eng.seed_artifact.id,
                      config=eng.cfg.to_json(), components=eng.components,
                      seed_components={c: eng.seed_artifact.get(c, "") for c in eng.components},
                      splits={"D_train": eng.split_names["train"], "n_train": len(eng.train_ids),
                              "D_pareto": eng.split_names["val"], "n_pareto": len(eng.val_ids)},
                      perfect_score=eng.perfect_score, selector=getattr(eng.selector, "name", type(eng.selector).__name__),
                      acceptance=eng.acceptance.name, stoppers=[type(s).__name__ for s in eng.stopper.stoppers],
                      llm_task=getattr(eng.llm_task, "name", None), llm_propose=getattr(eng.llm_propose, "name", None),
                      critic=type(eng.critic).__name__ if eng.critic is not None else None,
                      shadow_monitor={"splits": mon.splits, "k": mon.k} if mon is not None else None,
                      resumed_at=resumed_at)
        if resumed_at is not None:
            self.tr.event("note", None, what="resume",
                          text=f"resumed after iteration {resumed_at}; events of a killed iteration written "
                               f"before this line were discarded by the loop and are re-run below")

    def noise(self) -> None:
        cfg = self.eng.cfg
        if cfg.acceptance == "noise_margin":
            self.tr.event("noise", None, delta=cfg.noise_margin, mode="configured NoiseMargin (extension)", z=None,
                          detail="child accepted iff mean(after) > mean(before) + delta on the minibatch")
        else:
            self.tr.event("noise", None, delta=None, mode="none", z=None,
                          detail=f"GEPA measures no noise band: acceptance is '{cfg.acceptance}' on one draw "
                                 f"per (candidate, example) (paper Alg. 1 / reference StrictImprovementAcceptance)")

    def evaluation(self, round_: Optional[int], label: str, eb, ids: Sequence[str], *, split: str, phase: str,
                   charged: int, kind: str = "eval", with_text: bool = False, **extra: Any) -> None:
        trials = eb.trials or []
        by_task: dict[str, list[float]] = {}
        for t, s in zip(ids, eb.scores):
            by_task.setdefault(t, []).append(float(s))
        per_task = {t: sum(v) / len(v) for t, v in by_task.items()}
        toks = [tr.tokens for tr in trials if getattr(tr, "tokens", 0)]
        n = max(len(eb.scores), 1)
        summary = {"split": split, "S": sum(eb.scores) / n if eb.scores else 0.0,
                   "C": (sum(toks) / len(toks)) if toks else 0.0, "n_tasks": len(by_task), "k": 1,
                   "errors": round(eb.n_errors / n, 3), "missing": eb.n_infra, "sum": sum(eb.scores)}
        data = dict(candidate=label, summary=summary, per_task=per_task, trials=by_task, ids=list(ids),
                    scores=list(eb.scores), seeds=list(eb.seeds), phase=phase, rollouts_charged=charged)
        if eb.first_error:
            data["first_error"] = _clip(eb.first_error, 400)
        if with_text and trials:
            data["outputs"] = {f"{j}:{t}": _clip_mid(tr.output if not tr.error else f"(error) {tr.error}")
                               for j, (t, tr) in enumerate(zip(ids, trials))}
            data["feedback"] = {f"{j}:{t}": _clip(tr.feedback, 300) for j, (t, tr) in enumerate(zip(ids, trials))}
        data.update(extra)
        self.tr.event(kind, round_, **data)

    def round_start(self, i: int) -> None:
        self.tr.event("round_start", i, **self.state_snapshot())

    def selection(self, i: int, k: int, ids: Sequence[str], weights: dict) -> None:
        tot = sum(weights.values())
        self.tr.event("note", i, what="parent selection + minibatch",
                      selector=getattr(self.eng.selector, "name", type(self.eng.selector).__name__),
                      chosen=f"c{k}", sampling_weights={f"c{c}": w for c, w in sorted(weights.items())},
                      p_chosen=(weights.get(k, 0) / tot) if tot else None, minibatch_ids=list(ids),
                      sampler_epoch=self.eng.sampler.epoch)

    def analysis(self, i: int, k: int, comps: Sequence[str], refl: dict, rr_before: int) -> None:
        lines = [f"Parent c{k}; component(s) to rewrite (round-robin pointer {rr_before} -> "
                 f"{self.eng.state.rr[k]}): {', '.join(comps)}.",
                 "GEPA has no separate analysis step: the reflection LM diagnoses these records inside its call."]
        for c in comps:
            recs = refl.get(c) or []
            lines.append(f"\n[{c}] {len(recs)} reflective records (the <side_info> of the reflection prompt):")
            for j, r in enumerate(recs):
                lines.append(f"  #{j + 1} inputs: {_clip(r.get('Inputs', ''), 220)}")
                lines.append(f"      output: {_clip(r.get('Generated Outputs', ''), 260)}")
                lines.append(f"      feedback: {_clip(r.get('Feedback', ''), 400)}")
        self.tr.event("analysis", i, text="\n".join(lines), components=list(comps),
                      n_records={c: len(refl.get(c) or []) for c in comps})

    def reflective_proposal(self, i: int, label: str, k: int, parent: Artifact, child: Optional[Artifact], res,
                            comps: Sequence[str]) -> None:
        prompts = [res.prompts[c] for c in comps if c in res.prompts]
        replies = [res.raw[c] for c in comps if c in res.raw]
        sep = "\n\n=====\n\n"
        comm = [fenced_commentary(res.raw[c]) for c in comps if c in res.raw]
        err = "; ".join(f"{c}: {r}" for c, r in res.rejected.items()) or None
        diff = parent.diff(child) if child is not None else ""
        changed = parent.changed_files(child) if child is not None else []
        size = {c: {"lines_before": len(parent.get(c, "").splitlines()),
                    "lines_after": len(child.get(c, "").splitlines()) if child is not None else None}
                for c in comps}
        self.tr.proposal(i, label, parent=f"c{k}", prompt=sep.join(prompts), reply=sep.join(replies),
                         change=f"reflective rewrite of {', '.join(comps)} (parent c{k})",
                         hypothesis=_clip(" | ".join(x for x in comm if x), 1500), components=comps, diff=diff,
                         error=err, proposal_kind="reflective", reflection_calls=res.calls,
                         parsed={c: c in res.new_texts for c in comps}, files_actually_changed=changed,
                         finish_reasons={c: getattr(res, "finish", {}).get(c) for c in comps if c in res.raw},
                         unchanged_rewrite=(child is not None and not changed), size=size,
                         parent_artifact=parent.id, child_artifact=child.id if child is not None else None)

    def merge_proposal(self, i: int, label: str, prop, state) -> None:
        ci, cj = (state.candidates[p] for p in prop.parents)
        ca = state.candidates[prop.ancestor]
        comps = state.components
        src = {m: f"c{s}" for m, s in zip(sorted(comps), prop.sources)}
        self.tr.proposal(i, label, parent=f"c{prop.parents[0]}",
                         change=f"merge of c{prop.parents[0]} and c{prop.parents[1]} over common ancestor "
                                f"c{prop.ancestor}", components=[m for m in sorted(comps)],
                         diff=ci.diff(prop.candidate), proposal_kind="merge", parents=[f"c{p}" for p in prop.parents],
                         ancestor=f"c{prop.ancestor}", module_sources=src,
                         diff_vs_second_parent=cj.diff(prop.candidate), diff_vs_ancestor=ca.diff(prop.candidate),
                         child_artifact=prop.candidate.id)

    def gate_minibatch(self, i: int, label: str, before: Sequence[float], after: Sequence[float], accept: bool,
                       ids: Sequence[str]) -> None:
        cfg = self.eng.cfg
        sb, sa = float(sum(before)), float(sum(after))
        math = {"rule": {"strict_improvement": "sum(after) > sum(before)",
                         "improvement_or_equal": "sum(after) >= sum(before)",
                         "noise_margin": "mean(after) > mean(before) + delta"}.get(cfg.acceptance, cfg.acceptance),
                "minibatch_ids": list(ids), "before": list(before), "after": list(after), "sum_before": sb,
                "sum_after": sa, "delta_sum": sa - sb}
        if cfg.acceptance == "noise_margin":
            math.update(mean_before=sb / max(len(before), 1), mean_after=sa / max(len(after), 1),
                        delta=cfg.noise_margin)
        reason = (f"minibatch sum {sb:g} -> {sa:g} ({'+' if sa >= sb else ''}{sa - sb:g}): "
                  f"{'passes' if accept else 'fails'} {math['rule']}")
        self.tr.gate(i, label, accept, reason, math, gate="minibatch acceptance")

    def gate_merge(self, i: int, label: str, prop, accept: bool) -> None:
        sa = float(sum(prop.sub_after))
        math = {"rule": "sum(sub_after) >= max(sub_before_i, sub_before_j)", "subsample_ids": prop.subsample_ids,
                "sub_after": list(prop.sub_after), "sum_sub_after": sa,
                "sub_before": {f"c{p}": float(s) for p, s in zip(prop.parents, prop.sub_before)},
                "threshold": float(max(prop.sub_before))}
        reason = (f"merge subsample sum {sa:g} vs better parent {max(prop.sub_before):g}: "
                  f"{'passes' if accept else 'fails'} >=")
        self.tr.gate(i, label, accept, reason, math, gate="merge acceptance")

    def critic(self, i: int, label: str, verdict) -> None:
        self.tr.event("critic", i, candidate=label, accept=bool(verdict.accept), stage=getattr(verdict, "stage", ""),
                      objections=list(getattr(verdict, "objections", []))[:10])

    def decision(self, i: int, *, event: str, label: Optional[str], new_idx: Optional[int], best_before: int,
                 why: str, delta: Optional[dict] = None) -> None:
        st = self.eng.state
        agg = st.agg_scores()
        b = st.best_idx()
        extra: dict = {"event": event, "proposal": label, "added_to_pool": new_idx is not None,
                       "incumbent_val_before": round(agg[best_before], 4), "incumbent_val_after": round(agg[b], 4)}
        if new_idx is not None:
            extra["new_val_mean"] = round(agg[new_idx], 4)
            extra["new_parents"] = [f"c{p}" for p in st.parents[new_idx] if p is not None]
        if delta is not None:
            extra["frontier_delta"] = {"won": len(delta["won"]), "tied": len(delta["tied"]),
                                       "displaced": {k: [f"c{p}" for p in v] for k, v in
                                                     list(delta["displaced"].items())[:40]}}
        self.tr.decision(i, kept=self.label(new_idx), incumbent_before=f"c{best_before}", incumbent_after=f"c{b}",
                         why=why, **extra)

    def state(self, i: int) -> None:
        self.tr.event("state", i, **self.state_snapshot())

    def kept_if_new_incumbent(self, i: Optional[int], best_before: Optional[int]) -> None:
        st = self.eng.state
        b = st.best_idx()
        if best_before is None or b != best_before:
            self.tr.kept(i, f"c{b}", st.candidates[b], decision_score=st.agg_scores()[b])
            mon = self.tr.monitor
            if mon is not None:           # the audit's wall time must not count against Timeout / max_wall_s
                self.eng.credit_wall_time(getattr(mon, "last_elapsed_s", 0.0))

    def run_end(self, stop_reason: str) -> None:
        eng, st = self.eng, self.eng.state
        agg = st.agg_scores()
        b = st.best_idx()
        acc = sum(1 for e in st.trace if e.get("event") == "accepted")
        kinds = {}
        for e in st.trace:
            kinds[e.get("event", "-")] = kinds.get(e.get("event", "-"), 0) + 1
        self.tr.event("run_end", None, stop_reason=stop_reason, iterations=st.i + 1,
                      rollouts=st.counter.total, rollout_budget=eng.cfg.max_metric_calls,
                      rollouts_by_phase=dict(st.counter.by_phase), n_candidates=len(st.candidates),
                      events=kinds, n_accepted_reflective=acc, best=f"c{b}", best_val=agg[b], seed_val=agg[0],
                      best_artifact=st.candidates[b].id, n_proposals=st.n_proposals,
                      n_reflection_calls=st.n_reflection_calls, loop_usage=eng.usage_snapshot(),
                      shadow_usage=eng.shadow_usage_snapshot(),
                      monitor_wall_s_credited=round(getattr(eng, "monitor_wall_s", 0.0), 3))


def make_tracer(engine, out_dir, *, enabled: bool, max_text: int, monitor, domain, llm_task,
                splits: Optional[Sequence[str]], k: int, workers: int) -> GEPATracer:
    """Build the tracer; ``monitor`` is None/True (auto), False (off) or a ShadowMonitor."""
    tr = RunTracer(out_dir if (out_dir and enabled) else None, engine.method, max_text=max_text)
    if tr.enabled and monitor is not False:
        if isinstance(monitor, ShadowMonitor):
            tr.monitor = monitor
        else:
            sp = [s for s in (splits if splits is not None else default_shadow_splits(domain))
                  if s in domain.tasks.splits]
            if sp:
                tr.monitor = ShadowMonitor(domain, ShadowLLM(llm_task) if llm_task is not None else None, splits=sp,
                                           k=k, workers=workers)
    return GEPATracer(engine, tr)


__all__ = ["GEPATracer", "ShadowLLM", "default_shadow_splits", "fenced_commentary", "make_tracer", "SHADOW_PREFIX"]
