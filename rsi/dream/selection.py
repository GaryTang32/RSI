"""Selecting the next deployed policy: the paper's argmax with incumbent, or a held-out, noise-margin guard."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Sequence

import numpy as np

from ..core.gates import GateContext, MinGain, Scored, select


@dataclass
class SelectionResult:
    index: int
    reason: str
    values: list[float]
    details: dict = field(default_factory=dict)


def _mean(xs: Sequence[float], idx: Sequence[int]) -> float:
    return float(np.mean([xs[i] for i in idx])) if idx else float("-inf")


class Selector:
    """``m* = argmax_m V^m`` over all versions, incumbent (index 0) included, so ``V^{m*} >= V^0`` [paper:§3]."""

    name = "argmax"

    def __init__(self, include_incumbent: bool = True) -> None:
        self.include_incumbent = include_incumbent

    def dev_worlds(self, n_worlds: int) -> list[int]:
        return list(range(n_worlds))

    def holdout_worlds(self, n_worlds: int) -> list[int]:
        return []

    def select(self, reports: Sequence) -> SelectionResult:
        vals = [float(r.value) for r in reports]
        cands = [i for i in range(0 if self.include_incumbent else 1, len(reports)) if np.isfinite(vals[i])]
        if not cands:
            return SelectionResult(0, "no admissible candidate: keep incumbent", vals)
        best = max(vals[i] for i in cands)
        winners = [i for i in cands if vals[i] >= best - 1e-12]
        why = "argmax replay value (incumbent included)" if self.include_incumbent else \
            "argmax replay value over revisions only (incumbent excluded)"
        return SelectionResult(0 if 0 in winners else winners[0], why, vals, {"best": best})


class GuardedSelector(Selector):
    """Deploy the best development-world candidate only if its held-out gain exceeds
    ``delta = max(margin_floor, z * se(paired held-out differences))`` (:class:`rsi.core.gates.MinGain`)."""

    name = "guarded"

    def __init__(self, holdout_frac: float = 0.34, min_dev: int = 1, z: float = 2.0, margin_floor: float = 0.01,
                 include_incumbent: bool = True) -> None:
        super().__init__(include_incumbent)
        self.holdout_frac, self.min_dev, self.z, self.margin_floor = holdout_frac, min_dev, z, margin_floor

    def holdout_worlds(self, n_worlds: int) -> list[int]:
        """Every k-th world (k ~ 1/holdout_frac) from the oldest, keeping at least ``min_dev`` for development."""
        if n_worlds <= self.min_dev:
            return []
        k = max(2, int(round(1.0 / max(1e-9, self.holdout_frac))))
        hold = [i for i in range(n_worlds) if i % k == k - 1] or [n_worlds - 1]
        while n_worlds - len(hold) < self.min_dev and hold:
            hold.pop(0)
        return hold

    def dev_worlds(self, n_worlds: int) -> list[int]:
        hold = set(self.holdout_worlds(n_worlds))
        return [i for i in range(n_worlds) if i not in hold]

    def select(self, reports: Sequence) -> SelectionResult:
        n = len(reports[0].per_world) if reports else 0
        dev, hold = self.dev_worlds(n), self.holdout_worlds(n)
        dev_vals = [_mean(r.per_world, dev) for r in reports]
        cands = [i for i in range(1, len(reports)) if np.isfinite(dev_vals[i])]
        if not cands:
            return SelectionResult(0, "no candidate: keep incumbent", dev_vals)
        top = max(cands, key=lambda i: (dev_vals[i], -i))
        inc = reports[0]
        if hold:
            diffs = [reports[top].per_world[i] - inc.per_world[i] for i in hold]
            se = float(np.std(diffs, ddof=1) / math.sqrt(len(diffs))) if len(diffs) >= 2 else 0.0
            delta = max(self.margin_floor, self.z * se)
            gain_vals = (_mean(reports[top].per_world, hold), _mean(inc.per_world, hold))
        else:
            delta, gain_vals, diffs = self.margin_floor, (dev_vals[top], dev_vals[0]), []
        winner, verdicts = select([(top, Scored(score=gain_vals[0]))], Scored(score=gain_vals[1]), MinGain(),
                                  GateContext(delta=delta))
        if not self.include_incumbent and winner is None:
            winner = top
        idx = 0 if winner is None else int(winner)
        reason = verdicts[0][1].reason if verdicts else ""
        return SelectionResult(idx, ("deploy " if idx else "keep incumbent: ") + f"{reason} (held-out worlds {hold})",
                               dev_vals, {"delta": delta, "top_dev": top, "holdout": hold, "dev": dev,
                                          "holdout_gain": gain_vals[0] - gain_vals[1], "diffs": diffs})
