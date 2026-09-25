"""Stop conditions (``gepa/utils/stop_condition.py``), checked only at the top of an
iteration (so the last iteration can overshoot a metric-call budget by up to
2b + |D_pareto|, as in ``gepa.optimize``; use ``Config.budget_mode="hard"`` for the
``optimize_anything`` eval-server behaviour that stops mid-iteration).

A stopper is ``__call__(engine) -> Optional[str]`` returning a reason or None.
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

    def __call__(self, eng) -> Optional[str]:
        st = eng.state
        return self.budget.exhausted(rounds=st.i + 1, rollouts=st.counter.total, usd=eng.usd())


class Composite:
    def __init__(self, stoppers: Sequence[Callable], mode: str = "any") -> None:
        self.stoppers, self.mode = list(stoppers), mode

    def __call__(self, eng) -> Optional[str]:
        reasons = [s(eng) for s in self.stoppers]
        hits = [r for r in reasons if r]
        if self.mode == "any":
            return hits[0] if hits else None
        return "+".join(hits) if hits and len(hits) == len(reasons) else None
