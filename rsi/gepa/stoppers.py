"""Stop conditions (``gepa/utils/stop_condition.py``): ``stopper(engine) -> reason or None``."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Callable, Optional, Sequence


class _Limit:
    def __init__(self, n: int) -> None:
        self.n = n


class MaxMetricCalls(_Limit):
    def __call__(self, eng) -> Optional[str]:
        return "max_metric_calls" if eng.state.counter.total >= self.n else None


class MaxIterations(_Limit):
    def __call__(self, eng) -> Optional[str]:
        return "max_iterations" if eng.state.i + 1 >= self.n else None


class MaxCandidateProposals(_Limit):
    def __call__(self, eng) -> Optional[str]:
        return "max_candidate_proposals" if eng.state.n_proposals >= self.n else None


class Timeout:
    def __init__(self, seconds: float) -> None:
        self.seconds = seconds
        self.t0 = time.time()

    def credit(self, seconds: float) -> None:
        self.t0 += max(0.0, float(seconds))

    def __call__(self, eng) -> Optional[str]:
        return "timeout" if time.time() - self.t0 >= self.seconds else None


class FileStopper:
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
        return "no_improvement" if st.i - st.discovery_iter[st.best_idx()] >= self.patience and agg else None


class MaxReflectionCost:
    def __init__(self, usd: float, role: str = "reflection") -> None:
        self.usd, self.role = usd, role

    def __call__(self, eng) -> Optional[str]:
        u = eng.usage_snapshot().get(self.role, {})
        return "max_reflection_cost" if float(u.get("cost_usd", 0.0)) >= self.usd else None


class BudgetStopper:
    """Wraps :class:`rsi.core.Budget` (rounds = iterations, rollouts = metric calls)."""

    def __init__(self, budget) -> None:
        self.budget = budget

    def credit(self, seconds: float) -> None:
        if hasattr(self.budget, "credit"):
            self.budget.credit(seconds)

    def __call__(self, eng) -> Optional[str]:
        st = eng.state
        return self.budget.exhausted(rounds=st.i + 1, rollouts=st.counter.total, usd=eng.usd())


class ConsecutiveInfraFailures:
    """Stop after ``n`` consecutive iterations lost to rollout infra errors or failed reflection calls (extension)."""

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
    """Wall-time ``credit`` is forwarded to members that keep a clock, so shadow-monitor time never ends a run."""

    def __init__(self, stoppers: Sequence[Callable], mode: str = "any") -> None:
        self.stoppers, self.mode = list(stoppers), mode

    def credit(self, seconds: float) -> None:
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
