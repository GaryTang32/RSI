"""Per-step audit trace of a SoL-Pi research run (``rsi.trace`` format) + write-only shadow monitor.

The research protocol (spec B3.1) is mapped onto the uniform :mod:`rsi.trace` event kinds; one trace
*round* is one lineage iteration, so the rendered ``TRACE.md`` reads as the sequence of experiments:

round 0 (per driver round: the first trace round of that driver round)
    ``baseline``    base harness on the training screen (per task + raw trials, tokens / cost / steps)
    ``noise``       none: the dual gate compares single means against predeclared tolerances
    ``analysis``    oracle analysis: the opportunity estimate of every idea and the selected ideas
    ``monitor``     shadow score of the base on sealed splits (reference point)
one round per lineage iteration
    ``round_start`` driver round, idea (id / family / title / ground-truth kind, which the gate never
                    sees), lineage iteration, budgets (max_iters, ralph_max), the lineage history so far
    ``eval``        01 rollouts of the lineage's current harness (label ``rollouts``)
    ``analysis``    02 map-reduce evidence the proposer is shown
    ``proposal``    03+04 the proposed mechanism (change, variant, Ralph-loop repair errors, proposer
                    reply when an LLM wrote it) and the ACTUAL diff base -> candidate
    ``critic``      05 independent review verdict
    ``eval``        06 validation on the training screen (label ``screen``)
    ``gate``        the dual gate with every number (base, cand, tolerance, savings, per family)
    ``decision``    frozen / route back / abandoned; the incumbent (base) does not change inside a lineage
    ``state``       lineage history after the iteration
one final round per driver round
    ``gate``        held-out firewall verdict per frozen candidate (audit copy of the write-only sink;
                    the driver receives only the bool, lineages nothing)
    ``decision``    survivors, composition, incumbent base -> composed harness
    ``monitor``     shadow score of the composed harness on sealed splits
``run_end``         totals, usage (loop vs monitor)

The tracer only reads; nothing it records is returned to a lineage or the driver.
"""
from __future__ import annotations

from typing import Any, Optional

from ..core.llm import LLM
from ..trace import RunTracer, ShadowMonitor


class SolpiTracer:
    def __init__(self, tracer: RunTracer, monitor: Optional[ShadowMonitor] = None) -> None:
        self.tr = tracer
        if monitor is not None:
            tracer.monitor = monitor
        self.monitor = monitor
        self.r = 0                      # global trace round counter
        self.driver_round = 0
        self.lineages_done: list[dict] = []

    @property
    def enabled(self) -> bool:
        return self.tr.enabled

    # ------------------------------------------------------------------ driver level
    def run_start(self, drv) -> None:
        if not self.enabled:
            return
        ts = drv.domain.tasks
        self.tr.event("run_start", None, seed=drv.seed.short_id, config=drv.cfg.to_json(),
                      domain=getattr(drv.domain, "name", "?"), proposer=type(drv.proposer).__name__,
                      reviewer=type(drv.reviewer).__name__, gate_spec=drv.cfg.gate.__dict__,
                      gate_digest=drv.gate.digest, splits={s: len(v) for s, v in ts.splits.items()},
                      screen_families=sorted({t.family for t in drv.screen_tasks()}),
                      idea_pool=[{"id": i.id, "family": i.family, "title": i.title, "mechanism": i.mechanism,
                                  "grid": i.grid, "oracle": i.oracle, "kind": i.kind} for i in drv.pool.ideas],
                      shadow_splits=(self.monitor.splits if self.monitor else []))
        self.tr.event("noise", None, mode="none", delta=None, z=None,
                      detail="SoL-Pi has no noise band: the dual gate compares the candidate's mean screen metrics "
                             "with the base's against predeclared tolerances (capability) and a min relative gain "
                             f"(efficiency); k={drv.cfg.k} trial(s) per screen task")

    def base(self, rnd: int, art, ev, bm, est: dict, chosen: list) -> None:
        self.driver_round = rnd
        if not self.enabled:
            return
        self.r += 1
        self.tr.event("round_start", self.r, phase="driver round start", driver_round=rnd, base=art.short_id,
                      n_lineages=len(chosen), lineages_done_so_far=list(self.lineages_done))
        self.tr.event("baseline", self.r, candidate=f"base_r{rnd}", artifact=art.short_id, summary=ev.summary(),
                      per_task=ev.task_scores(), trials={t: [x.score for x in trs] for t, trs in ev.trials.items()},
                      metrics=bm.to_json())
        ranked = sorted(est.items(), key=lambda kv: (-kv[1], kv[0]))
        self.tr.event("analysis", self.r, text="Oracle analysis on base trajectories (share of avoidable work "
                                               "each idea targets):\n" +
                      "\n".join(f"- {i}: {v:.4f}" + ("  <- selected" if i in [c.id for c in chosen] else "")
                                for i, v in ranked),
                      oracle=est, chosen=[c.id for c in chosen])
        self.tr.kept(self.r, f"base_r{rnd}", art, decision_score=bm.agg.get("score"))

    # ------------------------------------------------------------------ lineage level
    def lineage_iter_start(self, lin, it: int, history: list[dict]) -> None:
        if not self.enabled:
            return
        self.r += 1
        idea = lin.idea
        self.tr.event("round_start", self.r, phase="lineage iteration", driver_round=self.driver_round,
                      idea={"id": idea.id, "family": idea.family, "title": idea.title, "mechanism": idea.mechanism,
                            "grid": idea.grid},
                      idea_kind_ground_truth=idea.kind, lineage_iteration=it, max_iters=lin.max_iters,
                      ralph_max=lin.ralph_max, sweep=lin.sweep, screen_tasks=len(lin.screen),
                      rollout_tasks=len(lin._rollout_tasks()),
                      history=[{k: h.get(k) for k in ("iteration", "change", "variant", "stage", "outcome")} |
                               {"gate_reason": (h.get("gate") or {}).get("reason")} for h in history],
                      base_metrics=lin.base_metrics.agg)

    def rollouts(self, current, ro, evidence: dict) -> None:
        if not self.enabled:
            return
        self.tr.evaluation(self.r, f"rollouts({current.short_id})", ro, phase="01 rollouts")
        self.tr.event("analysis", self.r, text="02 map-reduce evidence (mean over rollout trajectories):\n" +
                      "\n".join(f"- {k}: {v}" for k, v in evidence.items() if k != "worst"),
                      evidence=evidence)

    def proposal(self, lin, it: int, prop, ralph_errors: list[str], base) -> str:
        name = f"{lin.idea.id}.{it}"
        if not self.enabled:
            return name
        reply = prop.meta.get("reply") or prop.meta.get("code") or ""
        diff = base.diff(prop.artifact) if prop.artifact is not None else ""
        self.tr.proposal(self.r, name, parent=f"base({base.short_id})",
                         prompt=str(prop.meta.get("prompt") or ""), reply=str(reply), change=prop.change,
                         hypothesis=lin.idea.title, components=[lin.idea.mechanism or "free-form"], diff=diff,
                         error=prop.error, variant=prop.variant, ralph_errors=ralph_errors,
                         ralph_repairs=len(ralph_errors),
                         files_changed=sorted(base.changed_files(prop.artifact)) if prop.artifact else [],
                         proposer_usage=prop.usage.to_dict(), exhausted=bool(prop.meta.get("exhausted")))
        return name

    def review(self, name: str, ok: bool, why: str, reviewer) -> None:
        if self.enabled:
            self.tr.event("critic", self.r, candidate=name, stage=f"05 independent review ({type(reviewer).__name__})",
                          accept=ok, objections=[] if ok else [why], note=why)

    def validation(self, name: str, vr, m, g) -> None:
        if not self.enabled:
            return
        self.tr.evaluation(self.r, name, vr, phase="06 in-trajectory validation (training screen)",
                           metrics=m.to_json())
        self.tr.gate(self.r, name, g.accept, g.reason,
                     math={"rule": "accept iff every capability metric within tol of base AND some efficiency "
                                   "metric saves more than min_gain", "capability": g.capability,
                           "efficiency": g.efficiency, "per_family": g.per_family, "spec_digest": g.spec_digest})

    def decision(self, lin, name: str, row: dict, frozen: bool, next_action: str) -> None:
        if not self.enabled:
            return
        self.tr.decision(self.r, kept=name if frozen else None, incumbent_before=f"base({lin.base.short_id})",
                         incumbent_after=f"base({lin.base.short_id})",
                         why=f"{row.get('stage')}: {row.get('outcome')}"
                             + (f" ({row.get('error')})" if row.get("error") else "")
                             + f"; next: {next_action}", stage=row.get("stage"), outcome=row.get("outcome"))
        self.tr.event("state", self.r, idea=lin.idea.id, lineage_iteration=row.get("iteration"),
                      outcome=row.get("outcome"), next=next_action)

    def lineage_end(self, lin, result) -> None:
        self.lineages_done.append({"idea": lin.idea.id, "kind": lin.idea.kind, "frozen": result.frozen is not None,
                                   "iterations": len(result.iterations),
                                   "frozen_change": result.frozen.name if result.frozen else None})

    # ------------------------------------------------------------------ firewall + composition
    def firewall_and_compose(self, rnd: int, base, frozen: list, passed: dict, heldout: list, kept: list,
                             composed, conflicts: list, bm, cm, dropped: list, firewall_on: bool) -> None:
        if not self.enabled:
            return
        self.r += 1
        self.tr.event("round_start", self.r, phase="07 held-out firewall + composition", driver_round=rnd,
                      frozen=[f.name for f in frozen], lineages=list(self.lineages_done), firewall=firewall_on)
        hmap = {h["candidate"]: h for h in heldout}
        for f in frozen:
            h = hmap.get(f.name, {})
            self.tr.gate(self.r, f.name, bool(passed.get(f.name)),
                         ("held-out firewall: " + h.get("reason", "")) if h else "no firewall (passed through)",
                         math={"heldout_metrics": (h.get("metrics") or {}).get("agg"),
                               "heldout_base_metrics": (h.get("base_metrics") or {}).get("agg"),
                               "screen_metrics": f.metrics.agg, "idea_kind_ground_truth": f.idea.kind},
                         stage="held-out firewall (audit copy; lineages never see it)")
        if kept:
            self.tr.evaluation(self.r, f"composed_r{rnd}", {"metrics": cm.to_json()}, phase="composed on screen")
        after = f"composed_r{rnd}({composed.short_id})" if kept else f"base({base.short_id})"
        self.tr.decision(self.r, kept=",".join(f.idea.id for f in kept) or None,
                         incumbent_before=f"base({base.short_id})", incumbent_after=after,
                         why=(f"{len(kept)} of {len(frozen)} frozen candidates passed the firewall and were composed"
                              + (f"; conflicts {conflicts}" if conflicts else "")
                              + (f"; dropped by composition check {dropped}" if dropped else "")),
                         survivors=[f.name for f in kept], base_screen=bm.agg, composed_screen=cm.agg,
                         diff=base.diff(composed) if kept else "")
        if kept:
            self.tr.kept(self.r, f"composed_r{rnd}", composed, decision_score=cm.agg.get("score"))

    def run_end(self, extra: dict) -> None:
        if self.enabled:
            self.tr.event("run_end", None, lineages=list(self.lineages_done), **extra)


def make_tracer(domain, out_dir, *, enabled: bool, monitor: Any, llm_task: Optional[LLM], splits=None, k: int = 1,
                workers: int = 2) -> SolpiTracer:
    from ..metaharness.tracing import ShadowLLM, default_shadow_splits
    tr = RunTracer(out_dir if enabled else None, "solpi")
    mon = None
    if tr.enabled and monitor:
        if isinstance(monitor, ShadowMonitor):
            mon = monitor
        else:
            sp = list(splits) if splits else default_shadow_splits(domain)
            if sp:
                mon = ShadowMonitor(domain, ShadowLLM(llm_task) if llm_task is not None else None, splits=sp, k=k,
                                    workers=workers)
    return SolpiTracer(tr, mon)
