"""``run``: one agent's local evolution loop; the improved artifact is the harness plus ``genes/library.json``."""
from __future__ import annotations

import random
from pathlib import Path
from typing import Optional

from rsi.core import Artifact, Budget, Domain, Evaluator, ImprovementResult, Ledger, LLM, transfer_report
from rsi.trace import RunTracer, ShadowMonitor

from .agent import AgentNode
from .config import Config
from .inject import GeneRoutedDomain, Injector, library_artifact
from .signals import TaskSignalExtractor
from .store import LocalStore
from .tracing import eval_payload, library_fingerprint, loop_genes


def _zero_usage() -> dict:
    return {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "latency_s": 0.0, "total_tokens": 0}


def merged_usage(*llms: Optional[LLM]) -> dict:
    """Per-role usage summed over distinct LLM objects, plus ``_total`` (cached roles excluded)."""
    out: dict = {}
    seen = set()
    for llm in llms:
        if llm is None or id(llm) in seen:
            continue
        seen.add(id(llm))
        for role, u in llm.meter.snapshot().items():
            if role != "_total":
                cur = out.setdefault(role, _zero_usage())
                for k in cur:
                    cur[k] += u.get(k, 0)
    tot = _zero_usage()
    for r, u in out.items():
        if not r.endswith(":cached"):
            for k in tot:
                tot[k] += u[k]
    out["_total"] = tot
    return out


def run(domain: Domain, seed_artifact: Artifact, *, llm_task: Optional[LLM], llm_propose: Optional[LLM] = None,
        config: Optional[Config] = None, out_dir: Optional[str | Path] = None, hub=None,
        injector: Optional[Injector] = None, extractor: Optional[TaskSignalExtractor] = None,
        store: Optional[LocalStore] = None, executor=None, budget: Optional[Budget] = None,
        name: str = "agent0", monitor: Optional[ShadowMonitor] = None) -> ImprovementResult:
    """Run ``config.cycles`` cycles of one :class:`AgentNode` over the decision split and return the gene library.

    ``llm_propose=None`` gives a reuse-only agent; ``hub=None`` an offline one. Trace-only rollouts (baseline,
    shadow monitor) are excluded from the budget so tracing cannot change when the loop stops."""
    cfg = (config or Config()).resolved()
    out = Path(out_dir) if out_dir else None
    if out:
        out.mkdir(parents=True, exist_ok=True)
    store = store or LocalStore(out / "store" if out else None, node_id=name)
    ledger = Ledger(out / "ledger.jsonl" if out else None)
    tracer = RunTracer(out if (out and cfg.trace) else None, "evomap")
    agent = AgentNode(name, domain, seed_artifact, llm_task=llm_task, llm_propose=llm_propose, config=cfg,
                      store=store, hub=hub, injector=injector, extractor=extractor, ledger=ledger, executor=executor,
                      tracer=tracer)
    tasks = domain.tasks.split(cfg.split)
    if not tasks:
        raise ValueError(f"split {cfg.split!r} is empty")
    llms = list({id(x): x for x in (llm_task, llm_propose) if x}.values())
    overhead = [0.0]          # usd spent by trace-only evaluations

    def _spent() -> float:
        return sum(l.meter.total().cost_usd for l in llms)

    def _overhead(fn):
        u0 = _spent()
        try:
            return fn()
        finally:
            overhead[0] += _spent() - u0

    last_fp = library_fingerprint(loop_genes(store))
    if tracer.enabled:
        tracer.event("run_start", None, seed=seed_artifact.short_id, agent=name, domain=domain.name,
                     config=cfg.to_json(), hub=getattr(hub, "name", None),
                     splits={s: len(domain.tasks.splits[s]) for s in domain.tasks.splits},
                     decision_split=cfg.split, heldout_split=cfg.heldout_split,
                     resumed_library=[g.id for g in loop_genes(store)],
                     seed_files={p: seed_artifact[p][:600] for p in sorted(seed_artifact)},
                     what_improves="the gene library (genes/library.json) routed into the frozen harness")
        if cfg.trace_baseline_k > 0 and llm_task is not None:
            ev = Evaluator(domain, llm_task, workers=max(1, cfg.monitor_workers))
            r0 = _overhead(lambda: ev.evaluate(seed_artifact, cfg.split, cfg.trace_baseline_k, label="trace-baseline"))
            tracer.event("baseline", None, **eval_payload("seed", r0,
                                                          note="seed harness, no genes; trace only (not a loop input)"))
            tracer.event("note", None, what="baseline per-task scores",
                         per_task=r0.task_scores(), trials={t: [x.score for x in v] for t, v in r0.trials.items()})
        tracer.event("noise", None, delta=None, mode="none",
                     detail="the gene loop has no run-level noise band; each check carries its own: rsi-taskcheck "
                            "paired bootstrap CI, quarantine delta from the consumer's A/B trials, hub delta from "
                            "its bank trials (all recorded in the gate events)")
        if monitor is None and cfg.shadow_monitor and llm_task is not None and \
                any(s in domain.tasks.splits for s in ("holdout", "ood")):
            monitor = ShadowMonitor(GeneRoutedDomain(domain, injector=injector, extractor=extractor), llm_task,
                                    k=cfg.monitor_k, workers=max(1, cfg.monitor_workers))
        if monitor is not None:
            tracer.monitor = monitor
            _overhead(lambda: tracer.kept(0, "seed(no genes)", library_artifact(seed_artifact, loop_genes(store)),
                                          None))
    rng = random.Random(cfg.seed)
    order: list = []
    budget = budget or Budget(max_rounds=cfg.cycles)
    traj: list[dict] = []
    stop = "max_rounds"
    solved_window: list[int] = []
    for t in range(cfg.cycles):
        r = budget.exhausted(rounds=t, rollouts=agent.n_rollouts, usd=_spent() - overhead[0])
        if r:
            stop = r
            break
        if not order:
            order = list(tasks)
            if cfg.task_order == "shuffle":
                rng.shuffle(order)
        cr = agent.cycle(order.pop(0))
        solved_window = (solved_window + [int(cr.task_success)])[-10:]
        row = cr.to_json()
        row.update({"n_genes": len(loop_genes(store)), "rolling_solve_rate": sum(solved_window) / len(solved_window)})
        traj.append(row)
        if tracer.enabled and tracer.monitor is not None:
            fp = library_fingerprint(loop_genes(store))
            if fp != last_fp:
                last_fp = fp
                _overhead(lambda: tracer.kept(cr.cycle, f"library@c{cr.cycle}",
                                              library_artifact(seed_artifact, loop_genes(store)),
                                              row["rolling_solve_rate"]))
    agent.flush()
    genes = loop_genes(store)
    best = library_artifact(seed_artifact, genes)
    audit = store.audit()
    meta = {"config": cfg.to_json(), "n_genes": len(genes), "genes": [g.id for g in genes],
            "n_events": len(store.events), "n_capsules": len(store.capsules), "audit": audit.to_dict(),
            "proposer_calls": agent.proposer_calls, "rollouts": agent.n_rollouts, "quarantined": agent.n_quarantined,
            "quarantine_rejected": agent.n_quarantine_rejected, "mode": cfg.mode,
            "safe_mode_fixes": _safe_fixes(cfg), "evaluate_with": "rsi.evomap.GeneRoutedDomain(domain)",
            "trace_overhead_usd": overhead[0]}
    if tracer.enabled:
        tracer.event("run_end", None, stop_reason=stop, cycles=len(traj), library=[g.id for g in genes],
                     n_events=len(store.events), n_capsules=len(store.capsules), audit=audit.to_dict(),
                     rollouts=agent.n_rollouts, proposer_calls=agent.proposer_calls,
                     quarantined=agent.n_quarantined, quarantine_rejected=agent.n_quarantine_rejected,
                     solve_rate=sum(r["task_success"] for r in traj) / max(1, len(traj)),
                     usage=merged_usage(llm_task, llm_propose), trace_overhead_usd=overhead[0],
                     best_artifact=best.short_id)
    res = ImprovementResult(method="evomap", baseline=seed_artifact, best=best, ledger=ledger, trajectory=traj,
                            usage=merged_usage(llm_task, llm_propose), stop_reason=stop,
                            out_dir=str(out) if out else None, meta=meta)
    if out:
        res.save(out)       # before the live agent object goes into meta (it is not serializable)
        store.export_archive(out / "store.gepx.tgz")
        (out / "library.json").write_text(best["genes/library.json"])
    res.meta["agent"] = agent
    return res


def _safe_fixes(cfg: Config) -> list[str]:
    if cfg.mode != "safe":
        return []
    fixes = ["validation runner: no silent skips; empty validation list FAILS",
             "failed-capsule ban is relative: >= 2 failures AND more failures than successes (stochastic graders)",
             "keep rule: validation must be discriminative (lint + before/after) and the agent's own graded task "
             "solved",
             "memory-graph outcome = measured solidify outcome, recorded immediately (not the transcript's "
             "self-report)",
             "hub assets quarantined and A/B-tested on the agent's own held-out tasks before use",
             "distiller never adds a '--version' fallback validation"]
    if cfg.reject_memory:
        fixes.append("a hub asset the quarantine rejected is never re-tested (no re-sampling until it passes)")
    if not cfg.failure_distill:
        fixes.append("no failure-distilled repair genes (their validation is filtered to an empty list, so they "
                     "could never pass the safe keep rule)")
    return fixes


def evaluate_library(domain: Domain, llm: Optional[LLM], result: ImprovementResult, *,
                     splits=("evolve", "holdout", "ood"), k: int = 1, workers: int = 4,
                     injector: Optional[Injector] = None, extractor=None) -> dict:
    """``transfer_report`` over :class:`GeneRoutedDomain`: no genes vs the evolved library."""
    routed = GeneRoutedDomain(domain, injector=injector, extractor=extractor)
    return transfer_report(routed, llm, {"no_genes": result.baseline, "gene_library": result.best},
                           splits=splits, k=k, workers=workers)
