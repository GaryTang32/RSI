"""Public entry points: :func:`run` (any Domain + seed Artifact), :func:`optimize`
(plain dict candidates + a metric function), :func:`report` (sealed test reporting)."""
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
    """Evolvable files: ``domain.component_paths()`` if defined, else text-like files
    (never ``*.py`` - GEPA changes prompts, not control flow), else every file."""
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
    traj = [{k: e[k] for k in COMPACT_KEYS if k in e} for e in st.trace]
    n_acc = sum(1 for k in st.kinds if k == "reflective")
    n_merge = sum(1 for k in st.kinds if k == "merge")
    meta = {
        "config": eng.cfg.to_json(), "components": eng.components, "splits": eng.split_names,
        "n_train": len(eng.train_ids), "n_val": len(eng.val_ids),
        "rollouts": st.counter.total, "rollouts_by_phase": dict(st.counter.by_phase),
        "iterations": st.i + 1, "n_candidates": len(st.candidates), "n_proposals": st.n_proposals,
        "n_accepted": n_acc, "n_merges_accepted": n_merge, "n_reflection_calls": st.n_reflection_calls,
        "best_idx": b, "best_val": st.agg_scores()[b], "seed_val": st.agg_scores()[0],
        "candidates": st.candidates_table(), "frontier": st.frontier.to_json(),
        "best_history": st.best_history, "resumed_at": eng.resumed_at,
        "merge": eng.merge.get_state() if eng.merge else None,
        # merges built and scored on their subsample (accepted + rejected): what merge_cap_mode="hard" caps
        "n_merge_invocations": eng.merge.n_invocations if eng.merge else 0,
        "perfect_score": eng.perfect_score, "n_infra_errors": int(st.extra.get("n_infra", 0)),
        "n_infra_retries": getattr(eng.adapter, "n_infra_retries", 0),
        "selector": getattr(eng.selector, "name", type(eng.selector).__name__),
        # failures that would otherwise look like "no improvement found" (a run never hides them)
        "n_exec_errors": int(st.extra.get("n_exec_errors", 0)),
        "seed_val_error_rate": float(st.extra.get("seed_val_error_rate", 0.0)),
        "n_reflection_failed": int(st.extra.get("n_reflection_failed", 0)),
        "n_reflection_unparsed": int(st.extra.get("n_reflection_unparsed", 0)),
        "trace": str(eng.tr.tr.path) if eng.tr.enabled else None,
        "shadow_usage": eng.shadow_usage_snapshot(),
    }
    res = ImprovementResult(method=method, baseline=st.candidates[0], best=st.candidates[b], ledger=eng.ledger,
                            trajectory=traj, usage=eng.usage_snapshot(), stop_reason=eng.stop_reason,
                            out_dir=str(eng.out_dir) if eng.out_dir else None, meta=meta)
    res.state = st            # full search state (lineage, per-instance scores, trace)
    res.candidates = list(st.candidates)
    return res


def run(domain: Domain, seed_artifact: Artifact, *, llm_task: Optional[LLM] = None, llm_propose: Optional[LLM] = None,
        config: Optional[Config] = None, out_dir: Optional[str | Path] = None,
        components: Optional[Sequence[str]] = None, budget=None, proposer=None, adapter: Optional[DomainAdapter] = None,
        stoppers: Sequence[Callable] = (), callbacks: Sequence[Callable] = (), report_splits: Sequence[str] = (),
        report_k: int = 1, verbose: bool = False, method: str = "gepa", critic=None,
        selector=None, monitor=None) -> ImprovementResult:
    """Optimize the named text components of ``seed_artifact`` on ``domain`` with GEPA.

    Parameters
    ----------
    domain, seed_artifact:
        any :class:`rsi.core.Domain` and its starting artifact. D_train = the
        ``config.train_split`` split (default ``evolve``), D_pareto = ``config.val_split``
        (default ``val``; the train split when absent).
    llm_task:
        the frozen model the artifact runs on (passed to ``domain.execute``).
    llm_propose:
        the reflection LM (role ``reflection``); ignored when ``proposer`` is given.
    config:
        :class:`Config` (budget = ``max_metric_calls`` rollouts).
    out_dir:
        run directory (``state.json``, ``run_log.jsonl``, ``ledger.jsonl``, ``artifacts/``,
        ``gepa.stop``); calling ``run`` again on it resumes the run exactly.
    components:
        evolvable file names (default :func:`default_components`).
    budget:
        optional :class:`rsi.core.Budget` (usd / wall / STOP file / rollouts).
    report_splits:
        sealed splits (e.g. ``("test",)``) scored for the seed and the best candidate
        after the search, outside the budget (``result.meta["report"]``).
    critic:
        optional pre-evaluation screen with ``screen(diff, change) -> CriticVerdict``
        (e.g. ``rsi.core.LeakageCritic(domain.leakage_terms())``): rejected rewrites cost
        no rollouts for the child and are logged as ``critic_rejected`` (RRSI guard; off by default).
    selector:
        optional custom candidate selector overriding ``config.candidate_selection``: an object
        with ``select(state) -> candidate index`` (optionally ``get_state`` / ``set_state`` for
        resume), or a factory ``f(rng) -> selector`` that receives the engine's shared RNG.
    monitor:
        the write-only shadow monitor of the audit trace (``<out_dir>/trace.jsonl``, written when
        ``out_dir`` is given and ``config.trace``): None = ``config.shadow_monitor`` (auto: every new
        incumbent is scored on the domain's sealed holdout/ood splits, else its sealed test split),
        False = off, or an :class:`rsi.trace.ShadowMonitor`. Its numbers go only to the trace; its
        model calls are metered as ``shadow:*`` and excluded from the loop's spend and stoppers.

    Returns an :class:`rsi.core.ImprovementResult`: ``best`` = argmax mean D_pareto
    score, ``trajectory`` = one row per iteration, ``usage`` by role, ``meta`` with the
    rollout breakdown, candidate table and frontier; ``result.state`` is the
    :class:`~rsi.gepa.state.SearchState`.
    """
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
    """Sealed-split report (the optimizer never saw these splits): per-arm scores and
    paired differences against the first arm, via :func:`rsi.core.transfer_report`."""
    return transfer_report(domain, llm_task, arms, splits=tuple(splits), k=k, workers=workers)


# ------------------------------------------------------------------ quick start --
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
    """``gepa.optimize``-style quick start without writing a Domain.

    ``metric(candidate_dict, example) -> score | (score, feedback) | (score, feedback, output)``
    runs the user's system with the candidate's texts on one example and grades it;
    ``output`` (the system's response / trace text) is shown to the reflection LM.
    Examples can be any JSON-able objects. ``testset`` (optional) is sealed and scored
    only in the final report. Without ``valset``, D_pareto = the trainset (GEPA's
    multi-task / inference-time-search mode).
    """
    tasks, splits = [], {"evolve": [], "val": [], "test": []}
    for split, data in (("evolve", trainset), ("val", valset or []), ("test", testset or [])):
        for j, ex in enumerate(data):
            tid = f"{split}-{j:04d}"
            tasks.append(Task(tid, ex, None, "default"))
            splits[split].append(tid)
    if not splits["val"]:
        splits.pop("val")
    if not splits["test"]:
        splits.pop("test")
    suite = TaskSuite(tasks, splits, name="optimize")
    dom = MetricDomain(suite, metric, description)
    seed = Artifact(seed_candidate)
    # the suite built here names its splits evolve / val / test, whatever the config says
    cfg = replace(config or Config(), train_split="evolve", val_split="val" if valset else None)
    return run(dom, seed, llm_propose=llm_propose, config=cfg, out_dir=out_dir, components=list(seed_candidate),
               report_splits=("test",) if testset else ())
