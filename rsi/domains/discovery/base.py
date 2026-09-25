"""Shared base for CPU discovery domains.

Each domain is an :class:`rsi.core.Domain` whose artifact is a *program* written by a
discovery agent and whose single task is the problem instance. The locked evaluator
is :meth:`ProgramDomain.evaluate_program`, which runs the program (optionally in a
subprocess sandbox via :func:`rsi.core.sandbox.call_function`), checks it, and
returns an :class:`rsi.dream.agent.EvalOutcome` with a typed ``fail_class``:

* ``ok`` - evaluated successfully (score = task score, larger is better);
* ``compile_other`` - the program raised / did not produce output (repairable);
* ``constraint`` / ``correctness`` - infeasible or wrong output (repairable, score 0);
* ``timeout`` - exceeded the time limit (repairable);
* ``env_error`` - the evaluation infrastructure failed (hard).

``execute``/``grade`` wrap the same evaluator so the domain also works with
:class:`rsi.core.Evaluator`, ``transfer_report`` and every other method.
"""
from __future__ import annotations

import json
import time
from typing import Any, Optional

from ...core.artifact import Artifact
from ...core.domain import Domain, Execution
from ...core.sandbox import call_function
from ...core.tasks import Task, TaskSuite
from ...dream.agent import EvalOutcome


def single_task_suite(name: str, payload: Any = None) -> TaskSuite:
    t = Task(f"{name}-instance", payload, None, family=name)
    return TaskSuite([t], {"evolve": [t.id]}, name=name)


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

    # ---- to implement
    def check(self, output: Any, *, seed: int = 0) -> EvalOutcome:
        raise NotImplementedError

    def entry_payload(self, *, seed: int = 0) -> dict:
        return {}

    # ---- running the program
    def run_program(self, artifact: Artifact, *, seed: int = 0) -> tuple[Any, Optional[str], str, float]:
        """Returns (output, error, fail_class, seconds)."""
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
            return EvalOutcome(0.0, True, False, fc, err, 0, 1, {}, dt)
        try:
            ev = self.check(out, seed=seed)
        except Exception as e:  # noqa: BLE001 - a checker crash on bad output is the program's fault
            ev = EvalOutcome(0.0, True, False, "correctness", f"checker rejected output: {type(e).__name__}: {e}",
                             0, 1)
        ev.seconds += dt
        ev.diagnostics.setdefault("program_s", round(dt, 4))
        return ev

    # ---- rsi.core Domain API
    def execute(self, artifact: Artifact, task: Task, *, seed: int, llm=None) -> Execution:
        ev = self.evaluate_program(artifact, seed=seed)
        return Execution(output={"score": ev.score, "fail_class": ev.fail_class, "error": ev.error,
                                 "diagnostics": ev.diagnostics})

    def grade(self, task: Task, execution: Execution) -> tuple[float, str]:
        o = execution.output or {}
        if o.get("fail_class") != "ok":
            return 0.0, f"{o.get('fail_class')}: {str(o.get('error'))[:300]}"
        return float(o.get("score") or 0.0), json.dumps(o.get("diagnostics", {}), default=str)[:400]

    def directions(self) -> list[str]:
        return []

    def as_task(self):
        from ...dream.agent import DiscoveryTask

        dom = self

        class _Task(DiscoveryTask):
            name = dom.name
            domain = dom          # the underlying rsi.core Domain (sealed splits -> shadow monitor)

            def describe(self):
                return dom.describe()

            def seed_artifact(self):
                return dom.seed_artifact()

            def evaluate(self, artifact, *, seed=0):
                return dom.evaluate_program(artifact, seed=seed)

            def directions(self):
                return dom.directions()

            def editable(self):
                return [dom.program_file]

        return _Task()

    def seed_artifact(self) -> Artifact:  # pragma: no cover - per domain
        raise NotImplementedError
