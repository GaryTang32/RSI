"""Stop conditions (``gepa/utils/stop_condition.py``), checked only at the top of an
iteration (so the last iteration can overshoot a metric-call budget by up to
2b + |D_pareto|, as in ``gepa.optimize``; use ``Config.budget_mode="hard"`` for the
``optimize_anything`` eval-server behaviour that stops mid-iteration).

A stopper is ``__call__(engine) -> Optional[str]`` returning a reason or None. A
wall-clock stopper may also define ``credit(seconds)``: the engine calls it with the
write-only shadow monitor's wall time (``ShadowMonitor.last_elapsed_s``), so auditing
sealed splits never shortens a run (``Timeout``, ``BudgetStopper`` -> ``Budget.credit``).
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Callable, Optional, Sequence


class MaxMetricCalls:
    def __init__(self, n: int) -> None:
        self.n = n

    def __call__(self, eng) -> Optional[str]:
        return "max_metric_calls" if eng.state.counter.total >= self.n else None


class MaxIterations:
    def __init__(self, n: int) -> None:
        self.n = n

    def __call__(self, eng) -> Optional[str]:
        return "max_iterations" if eng.state.i + 1 >= self.n else None


class Timeout:
    def __init__(self, seconds: float) -> None:
        self.seconds = seconds
        self.t0 = time.time()

    def credit(self, seconds: float) -> None:
        """Give back wall time spent outside the loop's own work (the shadow monitor)."""
        self.t0 += max(0.0, float(seconds))

    def __call__(self, eng) -> Optional[str]:
        return "timeout" if time.time() - self.t0 >= self.seconds else None


class FileStopper:
    """Stop when ``<run_dir>/gepa.stop`` exists (the reference's stop file)."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def __call__(self, eng) -> Optional[str]:
        return "stop_file" if self.path.exists() else None


class ScoreThreshold:
    def __init__(self, threshold: float) -> None:
        self.threshold = threshold

    def __call__(self, eng) -> Optional[str]:
        st = eng.state
        return "score_threshold" if st.agg_scores()[st.best_idx()] >= self.threshold else None


class NoImprovement:
    """Stop after ``patience`` iterations without a new best validation mean."""

    def __init__(self, patience: int) -> None:
        self.patience = patience

    def __call__(self, eng) -> Optional[str]:
        st = eng.state
        agg = st.agg_scores()
        best = st.best_idx()
        last = st.discovery_iter[best]
        return "no_improvement" if st.i - last >= self.patience and agg else None


class MaxCandidateProposals:
    def __init__(self, n: int) -> None:
        self.n = n

    def __call__(self, eng) -> Optional[str]:
        return "max_candidate_proposals" if eng.state.n_proposals >= self.n else None


class MaxReflectionCost:
    """Stop when the reflection role has spent ``usd`` dollars."""

    def __init__(self, usd: float, role: str = "reflection") -> None:
        self.usd, self.role = usd, role

    def __call__(self, eng) -> Optional[str]:
        u = eng.usage_snapshot().get(self.role, {})
        return "max_reflection_cost" if float(u.get("cost_usd", 0.0)) >= self.usd else None


class BudgetStopper:
    """Wrap :class:`rsi.core.Budget` (rounds = iterations, rollouts = metric calls, usd, wall, STOP file)."""

    def __init__(self, budget) -> None:
        self.budget = budget

    def credit(self, seconds: float) -> None:
        if hasattr(self.budget, "credit"):
            self.budget.credit(seconds)

    def __call__(self, eng) -> Optional[str]:
        st = eng.state
        return self.budget.exhausted(rounds=st.i + 1, rollouts=st.counter.total, usd=eng.usd())


class ConsecutiveInfraFailures:
    """Stop after ``n`` consecutive iterations lost to infrastructure failures: the parent or child
    rollouts failed (``skip_infra_error``) or every reflection call failed with an LLM error
    (``no_proposal`` whose rejected outputs are all ``llm error: ...``). Extension: the reference
    has no such stopper (an exception there aborts the run, a swallowed one is scored 0)."""

    def __init__(self, n: int) -> None:
        if n < 1:
            raise ValueError("n must be >= 1")
        self.n = n

    @staticmethod
    def _is_infra(e: dict) -> bool:
        if e.get("event") == "skip_infra_error":
            return True
        rej = e.get("rejected_outputs") or {}
        return e.get("event") == "no_proposal" and bool(rej) and all(str(r).startswith("llm error")
                                                                     for r in rej.values())

    def __call__(self, eng) -> Optional[str]:
        tail = eng.state.trace[-self.n:]
        return "infra_outage" if len(tail) == self.n and all(self._is_infra(e) for e in tail) else None


class Composite:
    def __init__(self, stoppers: Sequence[Callable], mode: str = "any") -> None:
        self.stoppers, self.mode = list(stoppers), mode

    def credit(self, seconds: float) -> None:
        """Forward a wall-time credit to every member that keeps a wall clock."""
        for s in self.stoppers:
            fn = getattr(s, "credit", None)
            if callable(fn):
                fn(seconds)

    def __call__(self, eng) -> Optional[str]:
        reasons = [s(eng) for s in self.stoppers]
        hits = [r for r in reasons if r]
        if self.mode == "any":
            return hits[0] if hits else None
        return "+".join(hits) if hits and len(hits) == len(reasons) else None
