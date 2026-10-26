"""The problem interface: a Domain runs an artifact on a task (``execute``) and owns the locked grader (``grade``)."""
from __future__ import annotations

import copy
import json
import math
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .artifact import Artifact
from .llm import LLM
from .tasks import Task, TaskSuite


@dataclass
class Execution:
    """Raw result of running an artifact on one task, before grading."""

    output: Any = None
    trace: str = ""
    tokens: int = 0           # model tokens consumed by the artifact (its inference cost)
    cost_usd: float = 0.0
    steps: int = 0
    error: Optional[str] = None
    meta: dict = field(default_factory=dict)


@dataclass
class Trial:
    """One graded rollout: artifact x task x seed."""

    task_id: str
    seed: int
    score: float              # in the domain's score_range; larger is better
    feedback: str = ""
    output: Any = None
    trace: str = ""
    tokens: int = 0
    cost_usd: float = 0.0
    steps: int = 0
    latency_s: float = 0.0
    error: Optional[str] = None
    family: str = "default"
    meta: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.error is None

    def to_json(self, max_trace: int = 4000) -> dict:
        """JSON-safe dict of every field (``Trial(**d)`` round-trips); unserializable values become reprs."""
        d = {f: getattr(self, f) for f in self.__dataclass_fields__}
        if isinstance(d.get("trace"), str) and len(d["trace"]) > max_trace:
            d["trace"] = d["trace"][:max_trace] + "...[truncated]"
        d["output"] = _json_safe(d["output"])
        meta = _json_safe(d["meta"])
        d["meta"] = meta if isinstance(meta, dict) else {"repr": meta}
        return d


def _json_safe(x: Any) -> Any:
    try:
        json.dumps(x)
        return copy.deepcopy(x)
    except (TypeError, ValueError, RecursionError):
        pass
    if isinstance(x, dict):
        return {str(k): _json_safe(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_json_safe(v) for v in x]
    return repr(x)


class Domain:
    """Base class for problems: set ``self.tasks`` and implement :meth:`execute` and :meth:`grade`."""

    name: str = "domain"
    #: RRSI's component taxonomy K: component -> path/regex hints
    components: dict[str, list[str]] = {}
    #: RRSI's K_str: components that count as structural for novelty
    structural_components: tuple[str, ...] = ()
    score_range: tuple[float, float] = (0.0, 1.0)

    def __init__(self, tasks: TaskSuite) -> None:
        self.tasks = tasks

    def execute(self, artifact: Artifact, task: Task, *, seed: int, llm: Optional[LLM]) -> Execution:
        raise NotImplementedError

    def grade(self, task: Task, execution: Execution) -> tuple[float, str]:
        """Return ``(score, feedback)``. Must not depend on the artifact except via its output."""
        raise NotImplementedError

    @property
    def failure_score(self) -> float:
        """Score of a failed rollout: the bottom of :attr:`score_range`, so a crash never outscores a working one."""
        return float(self.score_range[0])

    def rollout(self, artifact: Artifact, task: Task, *, seed: int = 0, llm: Optional[LLM] = None) -> Execution:
        """Execute one rollout without grading; a crash becomes an :class:`Execution` with ``error`` set."""
        try:
            ex = self.execute(artifact, task, seed=seed, llm=llm)
            return ex if isinstance(ex, Execution) else Execution(output=ex)
        except (Exception, SystemExit) as e:  # noqa: BLE001 - an artifact crash is a graded failure, not a loop crash
            msg = str(e)
            # "infra:" errors keep their prefix: the Evaluator counts them as missing and never caches them
            err = msg if msg.startswith("infra:") else f"{type(e).__name__}: {e}"
            return Execution(error=err, trace=traceback.format_exc(limit=5))

    def grade_execution(self, task: Task, ex: Execution) -> tuple[float, str]:
        """``(score, feedback)`` of a rollout; errors and non-finite grades give :attr:`failure_score`."""
        fail = self.failure_score
        try:
            if ex.error is None:
                score, feedback = self.grade(task, ex)
                score = float(score)
                if not math.isfinite(score):
                    score, feedback = fail, f"grader error: non-finite score {score!r} (feedback: {feedback})"
            else:
                score, feedback = fail, f"execution error: {ex.error}"
        except (Exception, SystemExit) as e:  # noqa: BLE001
            score, feedback = fail, f"grader error: {type(e).__name__}: {e}"
        return score, feedback

    def run(self, artifact: Artifact, task: Task, *, seed: int = 0, llm: Optional[LLM] = None) -> Trial:
        """:meth:`rollout` then :meth:`grade_execution`: one graded :class:`Trial`."""
        t0 = time.time()
        ex = self.rollout(artifact, task, seed=seed, llm=llm)
        score, feedback = self.grade_execution(task, ex)
        return Trial(task_id=task.id, seed=seed, score=score, feedback=feedback, output=ex.output, trace=ex.trace,
                     tokens=ex.tokens, cost_usd=ex.cost_usd, steps=ex.steps, latency_s=time.time() - t0,
                     error=ex.error, family=task.family, meta=ex.meta)

    def describe(self) -> str:
        """Short brief for proposer LLMs: what the artifact is and how it is graded."""
        return f"Domain {self.name}: improve the artifact so it scores higher on held-out tasks."

    def leakage_terms(self, split: str = "evolve") -> list[str]:
        """Task ids, entities and literal answers of ``split``: strings a generic edit should never contain."""
        terms: list[str] = []
        for t in self.tasks.split(split, allow_sealed=True):
            terms.append(str(t.id))
            if t.target is not None and len(str(t.target)) >= 3:
                terms.append(str(t.target))
            terms.extend(str(x) for x in t.meta.get("entities", []))
        return sorted(set(terms))

    def smoke(self, artifact: Artifact, llm: Optional[LLM] = None) -> Optional[str]:
        """Run one smoke (or first evolve) task; return its error or None."""
        ids = self.tasks.splits.get("smoke") or self.tasks.splits.get("evolve", [])[:1]
        return (self.run(artifact, self.tasks.get(ids[0]), seed=0, llm=llm).error or None) if ids else None


class FunctionDomain(Domain):
    """A Domain from ``execute_fn(artifact, task, seed, llm)`` and ``grade_fn(task, output) -> score | (score, fb)``."""

    def __init__(self, tasks: TaskSuite, execute_fn: Callable[..., Any], grade_fn: Callable[..., Any], *,
                 name: str = "function-domain", description: str = "") -> None:
        super().__init__(tasks)
        self.execute_fn = execute_fn
        self.grade_fn = grade_fn
        self.name = name
        self._description = description

    def execute(self, artifact, task, *, seed, llm):
        out = self.execute_fn(artifact, task, seed, llm)
        return out if isinstance(out, Execution) else Execution(output=out)

    def grade(self, task, execution):
        r = self.grade_fn(task, execution.output)
        return r if isinstance(r, tuple) else (float(r), "")

    def describe(self) -> str:
        return self._description or super().describe()
