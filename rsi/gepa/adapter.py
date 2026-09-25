"""GEPA adapter over any :class:`rsi.core.Domain` (``gepa/core/adapter.py:GEPAAdapter``).

A GEPA candidate is an :class:`rsi.core.Artifact`; its evolvable *components* are
named files (prompts, skills, ...), everything else in the artifact is frozen.

:class:`DomainAdapter` provides the two adapter calls:

* ``evaluate(task_ids, candidate, capture_traces, seeds)`` -> :class:`EvalBatch`. Runs
  rollouts through :class:`rsi.core.Evaluator` (parallel, per-role metered, trial
  cache keyed by (artifact, task, seed)). Every requested (candidate, example) is one
  metric call, including repeats of a padded id - the engine does the counting;
* ``make_reflective_dataset(candidate, batch, components)`` ->
  ``{component: [{"Inputs", "Generated Outputs", "Feedback"}, ...]}`` built from
  :class:`rsi.core.Trial` (``trace`` + grader ``feedback``). A domain can provide a
  module-specific record via an optional hook
  ``domain.reflective_record(task, trial, component) -> dict`` (e.g. RuleWorld shows
  the reply module its upstream notes and only its own feedback).

``feedback="score_only"`` strips the grader's text (the ScoreOnlyReflection ablation:
records keep inputs and outputs, "Feedback" becomes just the score); ``"none"`` also
drops traces. Sealed splits (holdout/ood/test) are never touched here.
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Protocol, Sequence

from ..core.artifact import Artifact
from ..core.domain import Domain, Trial
from ..core.evaluate import Evaluator
from ..core.llm import LLM
from ..core.tasks import Task

FEEDBACK_MODES = ("full", "score_only", "none")
_SCORE_RE = re.compile(r"Score:\s*-?\d+(?:\.\d+)?(?:e-?\d+)?")


@dataclass
class EvalBatch:
    task_ids: list[str]
    outputs: list[Any]
    scores: list[float]
    trajectories: Optional[list[Trial]] = None
    objective_scores: Optional[list[dict]] = None
    num_metric_calls: int = 0
    seeds: list[int] = field(default_factory=list)


class GEPAAdapter(Protocol):
    def evaluate(self, task_ids: Sequence[str], candidate: Artifact, capture_traces: bool,
                 seeds: Sequence[int]) -> EvalBatch: ...

    def make_reflective_dataset(self, candidate: Artifact, batch: EvalBatch,
                                components: Sequence[str]) -> dict[str, list[dict]]: ...


def _fmt(x: Any) -> str:
    if isinstance(x, str):
        return x
    if isinstance(x, dict):
        return "; ".join(f"{k}: {v}" for k, v in x.items())
    return repr(x)


class DomainAdapter:
    """Adapter from an :class:`rsi.core.Domain` + frozen task LLM to GEPA."""

    def __init__(self, domain: Domain, llm_task: Optional[LLM] = None, *, workers: int = 1,
                 feedback: str = "full", max_trace_chars: int = 3000, cache_dir: Optional[str] = None,
                 record_fn: Optional[Callable[[Task, Trial, str], dict]] = None,
                 objective_fn: Optional[Callable[[Task, Trial], dict]] = None) -> None:
        if feedback not in FEEDBACK_MODES:
            raise ValueError(f"feedback must be one of {FEEDBACK_MODES}")
        self.domain = domain
        self.llm_task = llm_task
        self.evaluator = Evaluator(domain, llm_task, workers=1, cache_dir=cache_dir)
        self.workers = workers
        self.feedback = feedback
        self.max_trace_chars = max_trace_chars
        self.record_fn = record_fn or getattr(domain, "reflective_record", None)
        self.objective_fn = objective_fn
        ts = domain.tasks
        self._allowed = {i for name, ids in ts.splits.items() if not ts.is_sealed(name) for i in ids}

    # ------------------------------------------------------------------ splits --
    def ids(self, split: str) -> list[str]:
        """Task ids of a decision split (raises on sealed splits)."""
        return [t.id for t in self.domain.tasks.split(split)]

    # -------------------------------------------------------------- evaluation --
    def evaluate(self, task_ids: Sequence[str], candidate: Artifact, capture_traces: bool,
                 seeds: Sequence[int]) -> EvalBatch:
        tasks = [self.domain.tasks.get(t) for t in task_ids]
        bad = [t.id for t in tasks if t.id not in self._allowed]
        if bad:  # split discipline: sealed tasks never reach the optimizer
            raise PermissionError(f"tasks {bad[:3]} belong only to sealed splits")
        jobs = list(zip(tasks, seeds))
        if self.workers > 1 and len(jobs) > 1:
            with ThreadPoolExecutor(max_workers=self.workers) as ex:
                trials = list(ex.map(lambda j: self.evaluator.run_one(candidate, j[0], j[1]), jobs))
        else:
            trials = [self.evaluator.run_one(candidate, t, s) for t, s in jobs]
        objs = None
        if self.objective_fn is not None:
            objs = [self.objective_fn(t, tr) for t, tr in zip(tasks, trials)]
        elif any("objectives" in (tr.meta or {}) for tr in trials):
            objs = [dict((tr.meta or {}).get("objectives", {})) for tr in trials]
        return EvalBatch(list(task_ids), [tr.output for tr in trials], [float(tr.score) for tr in trials],
                         trials if capture_traces else None, objs, len(jobs), list(seeds))

    # ----------------------------------------------------- reflective dataset --
    def default_record(self, task: Task, trial: Trial) -> dict:
        out = _fmt(trial.output) if not trial.error else f"(execution error) {trial.error}"
        if trial.trace and self.feedback != "none":
            tr = trial.trace if len(trial.trace) <= self.max_trace_chars else \
                trial.trace[: self.max_trace_chars] + "\n...[trace truncated]"
            out = f"{out}\n\nExecution trace:\n{tr}"
        fb = f"Score: {trial.score:.3g}."
        if trial.feedback:
            fb += " " + trial.feedback
        return {"Inputs": _fmt(task.input), "Generated Outputs": out, "Feedback": fb}

    def make_reflective_dataset(self, candidate: Artifact, batch: EvalBatch,
                                components: Sequence[str]) -> dict[str, list[dict]]:
        assert batch.trajectories is not None, "reflective dataset needs capture_traces=True"
        out: dict[str, list[dict]] = {}
        for comp in components:
            recs = []
            for tid, tr in zip(batch.task_ids, batch.trajectories):
                task = self.domain.tasks.get(tid)
                rec = dict(self.record_fn(task, tr, comp)) if self.record_fn else self.default_record(task, tr)
                if self.feedback in ("score_only", "none"):
                    m = _SCORE_RE.match(str(rec.get("Feedback", "")))
                    rec["Feedback"] = (m.group(0) if m else f"Score: {tr.score:.3g}") + "."
                    if self.feedback == "none":
                        rec["Generated Outputs"] = str(rec.get("Generated Outputs", "")).split("\n")[0]
                recs.append(rec)
            out[comp] = recs
        return out

    @property
    def n_rollouts(self) -> int:
        return self.evaluator.n_rollouts
