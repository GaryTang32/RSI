"""Public entry point: :func:`run` (Config + domain + seed -> ImprovementResult)."""
from __future__ import annotations

import tempfile
import warnings
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


def _has_library(domain: Domain) -> bool:
    try:
        library_for(domain)
    except ValueError:
        return False
    return True


def _unwrap(llm: Optional[LLM]) -> Optional[LLM]:
    while isinstance(llm, CachedLLM):
        llm = llm.inner
    return llm


def make_proposer(domain: Domain, llm_propose: Optional[LLM], config: Config, *, kind: str = "auto",
                  leak_rate: float = 0.0) -> Proposer:
    """``auto``: ClaudeCLI (cached or not) -> coding agent, None or a plain MockLLM -> MockProposer,
    other LLMs -> RewriteProposer. The coding agent edits files in a workspace, so its sessions are not cached."""
    base = _unwrap(llm_propose)
    if kind == "mock" or llm_propose is None or (type(base) is MockLLM and kind == "auto" and _has_library(domain)):
        return MockProposer(library_for(domain), leak_rate=leak_rate, seed=config.seed)
    if kind == "agent" or (kind == "auto" and isinstance(base, ClaudeCLI)):
        if not isinstance(base, ClaudeCLI):
            raise ValueError("the coding-agent proposer needs a ClaudeCLI backend")
        if base is not llm_propose:
            warnings.warn("Meta-Harness: the coding-agent proposer runs the claude CLI directly; its sessions bypass "
                          "the LLM cache", stacklevel=2)
        return AgentProposer(base, timeout_s=config.proposer_timeout_s)
    return RewriteProposer(llm_propose)


def run(domain: Domain, seed_artifact: Artifact, *, llm_task: Optional[LLM] = None,
        llm_propose: Optional[LLM] = None, config: Optional[Config] = None, out_dir: Optional[str | Path] = None,
        baselines: Optional[dict[str, Artifact]] = None, proposer: Optional[Proposer] = None,
        proposer_kind: str = "auto", summarizer_llm: Optional[LLM] = None, leak_rate: float = 0.0,
        finalize_llm: Optional[LLM] = None, monitor=None) -> ImprovementResult:
    """Run Meta-Harness; ``best`` is the highest-score Pareto point on the search split."""
    cfg = config or Config()
    out = Path(out_dir) if out_dir else Path(tempfile.mkdtemp(prefix="metaharness_"))
    seed_name = seed_artifact.meta.get("name", "seed")
    pop = dict(baselines) if baselines else {seed_name: seed_artifact}
    prop = proposer or make_proposer(domain, llm_propose, cfg, kind=proposer_kind, leak_rate=leak_rate)
    summarizer = None
    if cfg.summaries == "always" or (cfg.summaries == "auto" and cfg.history_mode == "scores_summary"):
        is_mock = isinstance(prop, MockProposer)
        summarizer = prop.lib.summarize if is_mock else LLMSummarizer(summarizer_llm or llm_propose)
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
    screen = {"screened": loop.screen.n_screened, "rejected": loop.screen.n_rejected} if loop.screen else None
    res = ImprovementResult(
        method="metaharness", baseline=pop.get(seed_name, seed_artifact), best=loop.store.artifact(best_name),
        ledger=loop.ledger, trajectory=loop.iter_rows, usage=usage,
        stop_reason="eval_budget" if loop.budget_left() == 0 else "iterations", out_dir=str(out),
        meta={"frontier": fr, "best_system": best_name, "curve": loop.curve, "final": final,
              "n_evaluated": loop.n_evaluated, "n_proposed": loop.n_proposed, "config": cfg.to_json(),
              "n_reevaluations": loop.n_reevaluations, "store": str(loop.store.root), "screen": screen})
    loop.tr.run_end({"best_system": best_name, "stop_reason": res.stop_reason, "usage": usage,
                     "final_status": (final or {}).get("status"), "curve": loop.curve})
    res.loop = loop  # type: ignore[attr-defined]
    return res
