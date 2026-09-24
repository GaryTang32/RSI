"""The problem interface every method plugs into.

A :class:`Domain` answers one question: *how well does this artifact do on this
task?* It owns three things the self-improving loop must not control:

1. ``execute`` - run the artifact (a harness, a program, a training script...) on
   a task, with the frozen model, and return what happened;
2. ``grade``   - the locked grader (unit tests, exact match, simulator, bpb...);
3. the task suite with its split discipline.

To apply any method in this package to a new problem, subclass :class:`Domain`
(or use :class:`FunctionDomain` with two plain functions).
"""
from __future__ import annotations

import time
import traceback
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Optional

from .artifact import Artifact
from .llm import LLM
from .tasks import Task, TaskSuite


@dataclass
class Execution:
    """Raw result of running an artifact on one task (before grading)."""

    output: Any = None
    trace: str = ""           # human/LLM-readable log: reasoning, tool calls, tool outputs
    tokens: int = 0           # model tokens consumed by the *artifact* (its inference cost)
    cost_usd: float = 0.0
    steps: int = 0            # agent steps / model calls / iterations
    error: Optional[str] = None
    meta: dict = field(default_factory=dict)


@dataclass
class Trial:
    """One graded rollout: artifact x task x seed."""

    task_id: str
    seed: int
    score: float                   # in [0, 1] unless the domain says otherwise; larger is better
    feedback: str = ""             # textual feedback from the grader (GEPA's mu_f / "ASI")
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
        d = asdict(self)
        if isinstance(d.get("trace"), str) and len(d["trace"]) > max_trace:
            d["trace"] = d["trace"][:max_trace] + "...[truncated]"
        try:
            import json

            json.dumps(d["output"])
        except TypeError:
            d["output"] = repr(d["output"])
        return d


class Domain:
    """Base class for problems.

    Subclasses set ``self.tasks`` and implement :meth:`execute` and :meth:`grade`.
    Optional hooks refine what methods can do (component taxonomy for RRSI,
    leakage terms for critics, a description for proposers).
    """

    name: str = "domain"
    #: Component taxonomy (RRSI's K). Maps component -> list of path/regex hints.
    components: dict[str, list[str]] = {}
    #: Components that count as "structural" for novelty (RRSI's K_str).
    structural_components: tuple[str, ...] = ()
    #: Score range; most domains use [0, 1].
    score_range: tuple[float, float] = (0.0, 1.0)

    def __init__(self, tasks: TaskSuite) -> None:
        self.tasks = tasks

    # ---- to implement
    def execute(self, artifact: Artifact, task: Task, *, seed: int, llm: Optional[LLM]) -> Execution:
        raise NotImplementedError

    def grade(self, task: Task, execution: Execution) -> tuple[float, str]:
        """Return ``(score, feedback)``. Must not depend on the artifact except via its output."""
        raise NotImplementedError

    # ---- provided
    def run(self, artifact: Artifact, task: Task, *, seed: int = 0, llm: Optional[LLM] = None) -> Trial:
        t0 = time.time()
        try:
            ex = self.execute(artifact, task, seed=seed, llm=llm)
        except Exception as e:  # noqa: BLE001 - an artifact crash is a graded failure, not a loop crash
            ex = Execution(error=f"{type(e).__name__}: {e}", trace=traceback.format_exc(limit=5))
        try:
            score, feedback = self.grade(task, ex) if ex.error is None else (0.0, f"execution error: {ex.error}")
        except Exception as e:  # noqa: BLE001
            score, feedback = 0.0, f"grader error: {type(e).__name__}: {e}"
        return Trial(
            task_id=task.id, seed=seed, score=float(score), feedback=feedback, output=ex.output,
            trace=ex.trace, tokens=ex.tokens, cost_usd=ex.cost_usd, steps=ex.steps,
            latency_s=time.time() - t0, error=ex.error, family=task.family, meta=ex.meta,
        )

    def describe(self) -> str:
        """Short brief for proposer LLMs: what the artifact is and how it is graded."""
        return f"Domain {self.name}: improve the artifact so it scores higher on held-out tasks."

    def leakage_terms(self, split: str = "evolve") -> list[str]:
        """Strings a generic improvement should never contain: task ids, entity
        names, literal answers from the decision split. Used by leakage critics."""
        terms: list[str] = []
        for t in self.tasks.split(split, allow_sealed=True):
            terms.append(str(t.id))
            if t.target is not None and len(str(t.target)) >= 3:
                terms.append(str(t.target))
            terms.extend(str(x) for x in t.meta.get("entities", []))
        return sorted(set(terms))

    def smoke(self, artifact: Artifact, llm: Optional[LLM] = None) -> Optional[str]:
        """Cheap liveness check before a full evaluation. Return an error string or None."""
        ids = self.tasks.splits.get("smoke") or self.tasks.splits.get("evolve", [])[:1]
        for tid in ids[:1]:
            tr = self.run(artifact, self.tasks.get(tid), seed=0, llm=llm)
            if tr.error:
                return tr.error
        return None


class FunctionDomain(Domain):
    """Wrap two plain functions as a Domain.

    ``execute_fn(artifact, task, seed, llm) -> Execution | Any`` and
    ``grade_fn(task, output) -> float | (float, str)``.
    """

    def __init__(
        self,
        tasks: TaskSuite,
        execute_fn: Callable[..., Any],
        grade_fn: Callable[..., Any],
        *,
        name: str = "function-domain",
        description: str = "",
    ) -> None:
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
