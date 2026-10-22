"""Public entry points: :func:`run` (Domain + seed Artifact), :func:`optimize` (dicts + metric), :func:`report`."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

from ..core.artifact import Artifact
from ..core.domain import Domain, Execution
from ..core.llm import LLM
from ..core.run import ImprovementResult, transfer_report
from ..core.tasks import Task, TaskSuite
from .adapter import DomainAdapter
from .config import Config
from .engine import GEPAEngine

TEXT_SUFFIXES = (".md", ".txt", ".prompt", ".j2", ".jinja", ".yaml", ".yml", ".json")


def default_components(domain: Domain, seed: Artifact) -> list[str]:
    """``domain.component_paths()`` if defined, else text-like files, else non-``*.py`` files, else all."""
    hook = getattr(domain, "component_paths", None)
    if callable(hook):
        comps = [c for c in hook() if c in seed]
        if comps:
            return comps
    comps = [f for f in seed if f.endswith(TEXT_SUFFIXES)]
    return comps or [f for f in seed if not f.endswith(".py")] or list(seed)


COMPACT_KEYS = ("i", "event", "selected_program_candidate", "components", "subsample_scores", "new_subsample_scores",
                "new_program_idx", "merged_entities", "rollouts", "n_candidates", "best_idx", "best_val",
                "frontier_size")


def build_result(eng: GEPAEngine, method: str = "gepa") -> ImprovementResult:
    st = eng.state
    b = st.best_idx()
    agg = st.agg_scores()
    extra = st.extra
    meta = {
        "config": eng.cfg.to_json(), "components": eng.components, "splits": eng.split_names,
        "n_train": len(eng.train_ids), "n_val": len(eng.val_ids),
        "rollouts": st.counter.total, "rollouts_by_phase": dict(st.counter.by_phase),
        "iterations": st.i + 1, "n_candidates": len(st.candidates), "n_proposals": st.n_proposals,
        "n_accepted": st.kinds.count("reflective"), "n_merges_accepted": st.kinds.count("merge"),
        "n_reflection_calls": st.n_reflection_calls, "best_idx": b, "best_val": agg[b], "seed_val": agg[0],
        "candidates": st.candidates_table(), "frontier": st.frontier.to_json(),
        "best_history": st.best_history, "resumed_at": eng.resumed_at,
        "merge": eng.merge.get_state() if eng.merge else None,
        "n_merge_invocations": eng.merge.n_invocations if eng.merge else 0,
        "perfect_score": eng.perfect_score, "n_infra_errors": int(extra.get("n_infra", 0)),
        "n_infra_retries": getattr(eng.adapter, "n_infra_retries", 0),
        "selector": getattr(eng.selector, "name", type(eng.selector).__name__),
        "n_exec_errors": int(extra.get("n_exec_errors", 0)),
        "seed_val_error_rate": float(extra.get("seed_val_error_rate", 0.0)),
        "n_reflection_failed": int(extra.get("n_reflection_failed", 0)),
        "n_reflection_unparsed": int(extra.get("n_reflection_unparsed", 0)),
        "trace": str(eng.tr.tr.path) if eng.tr.enabled else None,
        "shadow_usage": eng.shadow_usage_snapshot(),
    }
    res = ImprovementResult(method=method, baseline=st.candidates[0], best=st.candidates[b], ledger=eng.ledger,
                            trajectory=[{k: e[k] for k in COMPACT_KEYS if k in e} for e in st.trace],
                            usage=eng.usage_snapshot(), stop_reason=eng.stop_reason,
                            out_dir=str(eng.out_dir) if eng.out_dir else None, meta=meta)
    res.state = st
    res.candidates = list(st.candidates)
    return res


def run(domain: Domain, seed_artifact: Artifact, *, llm_task: Optional[LLM] = None, llm_propose: Optional[LLM] = None,
        config: Optional[Config] = None, out_dir: Optional[str | Path] = None,
        components: Optional[Sequence[str]] = None, budget=None, proposer=None, adapter: Optional[DomainAdapter] = None,
        stoppers: Sequence[Callable] = (), callbacks: Sequence[Callable] = (), report_splits: Sequence[str] = (),
        report_k: int = 1, verbose: bool = False, method: str = "gepa", critic=None,
        selector=None, monitor=None) -> ImprovementResult:
    """Evolve the text components of ``seed_artifact`` on ``domain``; calling again on ``out_dir`` resumes."""
    cfg = config or Config()
    if adapter is None:
        adapter = DomainAdapter(domain, llm_task, workers=cfg.workers, feedback=cfg.feedback)
    comps = list(components or cfg.components or default_components(domain, seed_artifact))
    eng = GEPAEngine(adapter, seed_artifact, comps, llm_propose=llm_propose, config=cfg, out_dir=out_dir,
                     proposer=proposer, llm_task=llm_task, budget=budget, stoppers=stoppers, callbacks=callbacks,
                     verbose=verbose, method=method, critic=critic, selector=selector, monitor=monitor)
    eng.run()
    res = build_result(eng, method)
    if report_splits:
        res.meta["report"] = report(domain, llm_task, {"seed": res.baseline, "best": res.best}, splits=report_splits,
                                    k=report_k)
    if out_dir:
        res.save(out_dir)
    return res


def report(domain: Domain, llm_task: Optional[LLM], arms: dict[str, Artifact], *, splits: Sequence[str] = ("test",),
           k: int = 1, workers: int = 4) -> dict:
    """Sealed-split scores per arm and paired differences against the first arm."""
    return transfer_report(domain, llm_task, arms, splits=tuple(splits), k=k, workers=workers)


class MetricDomain(Domain):
    """Domain built from one ``metric(candidate_dict, example)`` function (see :func:`optimize`)."""

    name = "metric"

    def __init__(self, tasks: TaskSuite, metric: Callable, description: str = "") -> None:
        super().__init__(tasks)
        self.metric = metric
        self._description = description

    def execute(self, artifact, task, *, seed, llm):
        r = self.metric(dict(artifact.files), task.input)
        if isinstance(r, tuple):
            score = float(r[0])
            fb = str(r[1]) if len(r) > 1 else ""
            out = r[2] if len(r) > 2 else ""
        else:
            score, fb, out = float(r), "", ""
        return Execution(output=out, meta={"score": score, "feedback": fb})

    def grade(self, task, execution):
        return float(execution.meta["score"]), str(execution.meta["feedback"])

    def describe(self) -> str:
        return self._description or super().describe()


def optimize(seed_candidate: dict[str, str], trainset: Sequence[Any], valset: Optional[Sequence[Any]] = None, *,
             metric: Callable[[dict, Any], Any], llm_propose: LLM, config: Optional[Config] = None,
             testset: Optional[Sequence[Any]] = None, out_dir: Optional[str | Path] = None,
             description: str = "") -> ImprovementResult:
    """``gepa.optimize``-style quick start; ``metric(candidate, example) -> score | (score, feedback[, output])``."""
    tasks, splits = [], {"evolve": [], "val": [], "test": []}
    for split, data in (("evolve", trainset), ("val", valset or []), ("test", testset or [])):
        for j, ex in enumerate(data):
            tid = f"{split}-{j:04d}"
            tasks.append(Task(tid, ex, None, "default"))
            splits[split].append(tid)
    for name in ("val", "test"):
        if not splits[name]:
            splits.pop(name)
    dom = MetricDomain(TaskSuite(tasks, splits, name="optimize"), metric, description)
    seed = Artifact(seed_candidate)
    cfg = replace(config or Config(), train_split="evolve", val_split="val" if valset else None)
    return run(dom, seed, llm_propose=llm_propose, config=cfg, out_dir=out_dir, components=list(seed_candidate),
               report_splits=("test",) if testset else ())
