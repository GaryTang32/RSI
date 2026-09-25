"""Public entry point: :func:`run` (Config + domain + seed -> ImprovementResult)."""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Optional

from ..core.artifact import Artifact
from ..core.domain import Domain
from ..core.llm import LLM, ClaudeCLI, CachedLLM, MockLLM
from ..core.run import ImprovementResult
from .config import Config
from .loop import MetaHarnessLoop
from .mock import MockProposer, library_for
from .proposer import AgentProposer, LLMSummarizer, Proposer, RewriteProposer
from .tracing import make_tracer, shadow_usage


def make_proposer(domain: Domain, llm_propose: Optional[LLM], config: Config, *, kind: str = "auto",
                  leak_rate: float = 0.0) -> Proposer:
    """``auto``: ClaudeCLI -> coding agent (paper); any other LLM -> RewriteProposer;
    None / plain MockLLM -> offline MockProposer for the domain's program library (a plain
    MockLLM on a domain without a library is used as a scripted RewriteProposer backend)."""
    has_lib = True
    try:
        library_for(domain)
    except ValueError:
        has_lib = False
    if kind == "mock" or llm_propose is None or (type(llm_propose) is MockLLM and kind == "auto" and has_lib):
        return MockProposer(library_for(domain), leak_rate=leak_rate, seed=config.seed)
    if kind == "agent" or (kind == "auto" and isinstance(llm_propose, ClaudeCLI)):
        cli = llm_propose.inner if isinstance(llm_propose, CachedLLM) else llm_propose
        if not isinstance(cli, ClaudeCLI):
            raise ValueError("the coding-agent proposer needs a ClaudeCLI backend")
        return AgentProposer(cli, timeout_s=config.proposer_timeout_s)
    return RewriteProposer(llm_propose)


def run(domain: Domain, seed_artifact: Artifact, *, llm_task: Optional[LLM] = None,
        llm_propose: Optional[LLM] = None, config: Optional[Config] = None, out_dir: Optional[str | Path] = None,
        baselines: Optional[dict[str, Artifact]] = None, proposer: Optional[Proposer] = None,
        proposer_kind: str = "auto", summarizer_llm: Optional[LLM] = None, leak_rate: float = 0.0,
        finalize_llm: Optional[LLM] = None, monitor=None) -> ImprovementResult:
    """Run Meta-Harness on any :class:`rsi.core.Domain`.

    Parameters
    ----------
    domain, seed_artifact:
        the problem and a valid starting harness. ``baselines`` (default
        ``{seed name: seed_artifact}``) is the initial population ``H0``.
    llm_task:
        the frozen base model the harness calls (``None`` lets the domain pick its default).
    llm_propose:
        the proposer model: ``ClaudeCLI`` -> coding agent reading the history
        directory (the paper's proposer); any other LLM -> :class:`RewriteProposer`
        with rendered history; ``None`` -> deterministic :class:`MockProposer`.
    config:
        :class:`Config` (iterations, k, history_mode, objectives, budget, leakage screen ...).
    out_dir:
        run directory (the experience store lives in ``out_dir/store``).

    monitor:
        the write-only shadow monitor of the audit trace (``<out_dir>/trace.jsonl``, written when
        ``out_dir`` is given and ``config.trace``): None = ``config.shadow_monitor`` (auto: every new
        frontier ``_best`` is scored on the domain's sealed holdout/ood splits), False = off, or an
        :class:`rsi.trace.ShadowMonitor`. Its numbers go only to the trace; its model calls are metered
        as ``shadow:*`` and reported separately in ``usage["shadow_monitor"]``.

    Returns an :class:`rsi.core.ImprovementResult` whose ``best`` is the highest-score
    Pareto point on the search split; ``meta`` holds the frontier, the evaluation
    curve (best-so-far vs #evaluations) and, if ``config.finalize``, the one-time
    test report.
    """
    cfg = config or Config()
    out = Path(out_dir) if out_dir else Path(tempfile.mkdtemp(prefix="metaharness_"))
    seed_name = seed_artifact.meta.get("name", "seed")
    pop = dict(baselines) if baselines else {seed_name: seed_artifact}
    prop = proposer or make_proposer(domain, llm_propose, cfg, kind=proposer_kind, leak_rate=leak_rate)
    summarizer = None
    if cfg.summaries == "always" or (cfg.summaries == "auto" and cfg.history_mode == "scores_summary"):
        if isinstance(prop, MockProposer):
            summarizer = prop.lib.summarize
        else:
            summarizer = LLMSummarizer(summarizer_llm or llm_propose)
    # Best-of-N ("independent samples from the seed"): the seed_only view shows the run's seed harness only
    seeds = [seed_name] if seed_name in pop else list(pop)
    loop = MetaHarnessLoop(domain, llm_task=llm_task, proposer=prop, config=cfg, out_dir=out, baselines=pop,
                           summarizer=summarizer, seed_names=seeds)
    if out_dir is not None and cfg.trace:
        loop.tr = make_tracer(loop, out, enabled=True, monitor=cfg.shadow_monitor if monitor is None else monitor,
                              llm_task=llm_task, splits=cfg.shadow_splits, k=cfg.shadow_k,
                              workers=cfg.shadow_workers)
    loop.tr.run_start(seeds, extra={"proposer_kind": proposer_kind, "leak_rate": leak_rate,
                                    "llm_task": getattr(llm_task, "name", None),
                                    "llm_propose": getattr(llm_propose, "name", None)})
    loop.run()
    final = loop.finalize(llm=finalize_llm) if cfg.finalize else None
    fr = loop.store.frontier()
    best_name = fr["_best"]["system"] if fr.get("_best") else seed_name
    usage = {"proposer": loop.proposer_usage.to_dict()}
    if llm_task is not None:
        usage["task"] = llm_task.meter.snapshot()
    if llm_propose is not None:
        usage["propose_llm"] = llm_propose.meter.snapshot()
    usage["_total"] = loop.proposer_usage.to_dict()
    usage["shadow_monitor"] = shadow_usage(loop.tr)
    res = ImprovementResult(
        method="metaharness", baseline=pop.get(seed_name, seed_artifact), best=loop.store.artifact(best_name),
        ledger=loop.ledger, trajectory=loop.iter_rows, usage=usage,
        stop_reason="eval_budget" if loop.budget_left() == 0 else "iterations", out_dir=str(out),
        meta={"frontier": fr, "best_system": best_name, "curve": loop.curve, "final": final,
              "n_evaluated": loop.n_evaluated, "n_proposed": loop.n_proposed, "config": cfg.to_json(),
              "store": str(loop.store.root), "screen": ({"screened": loop.screen.n_screened,
                                                         "rejected": loop.screen.n_rejected} if loop.screen else None)})
    loop.tr.run_end({"best_system": best_name, "stop_reason": res.stop_reason, "usage": usage,
                     "final_status": (final or {}).get("status"), "curve": loop.curve})
    res.loop = loop  # type: ignore[attr-defined]
    return res
