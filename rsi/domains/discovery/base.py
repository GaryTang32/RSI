"""ProgramDomain: a discovery problem whose artifact is a program, graded by a locked ``evaluate_program``."""
from __future__ import annotations

import json
import random
import time
from typing import Any, Optional, Sequence

from ...core.artifact import Artifact
from ...core.domain import Domain, Execution
from ...core.sandbox import call_function
from ...core.tasks import Task, TaskSuite
from ...dream.agent import AttemptContext, DiscoveryTask, EvalOutcome


def failure(fail_class: str, error: str, diagnostics: Optional[dict] = None, seconds: float = 0.0) -> EvalOutcome:
    """Score-0 failed evaluation (0 of 1 outputs valid)."""
    return EvalOutcome(0.0, True, False, fail_class, error, 0, 1, {} if diagnostics is None else diagnostics, seconds)


def execution(ev: EvalOutcome) -> Execution:
    return Execution(output={"score": ev.score, "fail_class": ev.fail_class, "error": ev.error,
                             "diagnostics": ev.diagnostics})


def pick_mechanism(ctx: Optional[AttemptContext], rng: random.Random, mechanisms: Sequence[str],
                   p_switch: float) -> str:
    """The branch's assigned direction (else a random one); later attempts switch with probability ``p_switch``."""
    mech = (ctx.direction.get("direction") if ctx and ctx.direction else None) or rng.choice(mechanisms)
    if ctx and ctx.attempt > 0 and rng.random() < p_switch:
        mech = rng.choice(mechanisms)
    return mech


def parent_measured(ctx: Optional[AttemptContext], key: str, default):
    """``diagnostics[key]`` of the parent's ``eval/score.json`` (its measured result), else ``default``."""
    if ctx is None:
        return default
    try:
        sj = json.loads(ctx.parent_workspace.get("eval/score.json") or "{}")
    except json.JSONDecodeError:
        return default
    return (sj.get("diagnostics") or {}).get(key) or default


def single_task_suite(name: str, payload: Any = None) -> TaskSuite:
    t = Task(f"{name}-instance", payload, None, family=name)
    return TaskSuite([t], {"evolve": [t.id]}, name=name)


class ProgramTask(DiscoveryTask):
    """A domain as the discovery loop's task: its ``evaluate_program`` is the locked evaluator."""

    def __init__(self, dom: Domain, name: str, program_file: Optional[str] = None) -> None:
        self._dom, self.name, self._program_file = dom, name, program_file

    def describe(self) -> str:
        return self._dom.describe()

    def seed_artifact(self) -> Artifact:
        return self._dom.seed_artifact()

    def evaluate(self, artifact: Artifact, *, seed: int = 0) -> EvalOutcome:
        return self._dom.evaluate_program(artifact, seed=seed)

    def directions(self) -> list[str]:
        return self._dom.directions()

    def editable(self) -> Optional[list[str]]:
        return None if self._program_file is None else [self._program_file]


class ProgramDomain(Domain):
    """A discovery problem whose artifact is a program with an entry function."""

    name = "program"
    program_file = "program.py"
    entry = "main"
    components = {"program": ["*.py"]}

    def __init__(self, tasks: Optional[TaskSuite] = None, *, sandboxed: bool = True, timeout_s: float = 30.0) -> None:
        super().__init__(tasks or single_task_suite(self.name))
        self.sandboxed = sandboxed
        self.timeout_s = timeout_s

    def check(self, output: Any, *, seed: int = 0) -> EvalOutcome:
        raise NotImplementedError

    def entry_payload(self, *, seed: int = 0) -> dict:
        return {}

    def run_program(self, artifact: Artifact, *, seed: int = 0) -> tuple[Any, Optional[str], str, float]:
        """``(output, error, fail_class, seconds)``; sandboxed via :func:`rsi.core.sandbox.call_function`."""
        code = artifact.get(self.program_file)
        if code is None:
            return None, f"{self.program_file} missing", "compile_other", 0.0
        payload = self.entry_payload(seed=seed)
        t0 = time.time()
        if self.sandboxed:
            extra = {k: v for k, v in artifact.files.items() if k != self.program_file and k.endswith(".py")}
            out, rr = call_function(code, self.entry, payload, timeout_s=self.timeout_s, extra_files=extra)
            dt = time.time() - t0
            if rr.timed_out:
                return None, f"timeout after {self.timeout_s}s", "timeout", dt
            if out is None:
                return None, rr.tail(8)[-600:] or "program produced no result", "compile_other", dt
            return out, None, "ok", dt
        ns: dict = {"__name__": "candidate"}
        try:
            exec(compile(code, self.program_file, "exec"), ns)  # noqa: S102 - trusted mock-agent programs
            out = ns[self.entry](**payload)
            out = json.loads(json.dumps(out, default=float))
        except Exception as e:  # noqa: BLE001
            return None, f"{type(e).__name__}: {e}", "compile_other", time.time() - t0
        return out, None, "ok", time.time() - t0

    def evaluate_program(self, artifact: Artifact, *, seed: int = 0) -> EvalOutcome:
        out, err, fc, dt = self.run_program(artifact, seed=seed)
        if err is not None:
            return failure(fc, err, seconds=dt)
        try:
            ev = self.check(out, seed=seed)
        except Exception as e:  # noqa: BLE001 - a checker crash on bad output is the program's fault
            ev = failure("correctness", f"checker rejected output: {type(e).__name__}: {e}")
        ev.seconds += dt
        ev.diagnostics.setdefault("program_s", round(dt, 4))
        return ev

    def execute(self, artifact: Artifact, task: Task, *, seed: int, llm=None) -> Execution:
        return execution(self.evaluate_program(artifact, seed=seed))

    def grade(self, task: Task, execution: Execution) -> tuple[float, str]:
        o = execution.output or {}
        if o.get("fail_class") != "ok":
            return 0.0, f"{o.get('fail_class')}: {str(o.get('error'))[:300]}"
        return float(o.get("score") or 0.0), json.dumps(o.get("diagnostics", {}), default=str)[:400]

    def directions(self) -> list[str]:
        return []

    def as_task(self) -> DiscoveryTask:
        task = ProgramTask(self, self.name, self.program_file)
        task.domain = self  # the underlying rsi.core Domain (sealed splits -> shadow monitor)
        return task

    def seed_artifact(self) -> Artifact:  # pragma: no cover - per domain
        raise NotImplementedError
