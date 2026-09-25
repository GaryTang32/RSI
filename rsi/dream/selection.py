"""Selecting the next deployed policy.

* :class:`Selector` - the paper's rule: ``m* = argmax_m V^m`` over all evaluated
  versions *including the incumbent* ``pi_t^0``, so ``V^{m*} >= V^0`` on replay
  [paper:§3 p.6]. ``include_incumbent=False`` is the E4 ablation.
* :class:`GuardedSelector` - the RRSI-guarded extension the overview asks for
  ("keep some past searches aside for checking, and ignore wins smaller than the
  noise" [doc]): worlds are split into development worlds (the developer's feedback
  and the ranking) and held-out worlds; the best development candidate replaces the
  incumbent only if its mean held-out gain over the incumbent exceeds a noise
  margin ``delta = max(margin_floor, z * se(paired held-out differences))``. The gate
  is :class:`rsi.core.gates.MinGain`, applied through :func:`rsi.core.gates.select`.
"""
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
    """argmax of the replay value over versions, incumbent (index 0) included."""

    name = "argmax"

    def __init__(self, include_incumbent: bool = True) -> None:
        self.include_incumbent = include_incumbent

    def dev_worlds(self, n_worlds: int) -> list[int]:
        return list(range(n_worlds))

    def holdout_worlds(self, n_worlds: int) -> list[int]:
        return []

    def select(self, reports: Sequence) -> SelectionResult:
        vals = [float(r.value) for r in reports]
        cands = list(range(len(reports))) if self.include_incumbent else list(range(1, len(reports)))
        cands = [i for i in cands if np.isfinite(vals[i])]
        if not cands:
            return SelectionResult(0, "no admissible candidate: keep incumbent", vals)
        best = max(vals[i] for i in cands)
        winners = [i for i in cands if vals[i] >= best - 1e-12]
        idx = 0 if 0 in winners else winners[0]
        why = "argmax replay value (incumbent included)" if self.include_incumbent else \
            "argmax replay value over revisions only (incumbent excluded)"
        return SelectionResult(idx, why, vals, {"best": best})


class GuardedSelector(Selector):
    """Held-out worlds + noise margin (RRSI-style guard on Dream-RSI selection)."""

    name = "guarded"

    def __init__(self, holdout_frac: float = 0.34, min_dev: int = 1, z: float = 2.0, margin_floor: float = 0.01,
                 include_incumbent: bool = True) -> None:
        super().__init__(include_incumbent)
        self.holdout_frac, self.min_dev, self.z, self.margin_floor = holdout_frac, min_dev, z, margin_floor

    def holdout_worlds(self, n_worlds: int) -> list[int]:
        """Every k-th world (k ~ 1/holdout_frac), counted from the oldest, is held out,
        so held-out worlds come from all phases of the run; at least ``min_dev`` worlds
        stay for development."""
        if n_worlds <= self.min_dev:
            return []
        k = max(2, int(round(1.0 / max(1e-9, self.holdout_frac))))
        hold = [i for i in range(n_worlds) if i % k == k - 1]
        if not hold:
            hold = [n_worlds - 1]
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
        if not hold:
            delta = self.margin_floor
            gain_vals = (dev_vals[top], dev_vals[0])
            diffs = []
        else:
            diffs = [reports[top].per_world[i] - inc.per_world[i] for i in hold]
            se = float(np.std(diffs, ddof=1) / math.sqrt(len(diffs))) if len(diffs) >= 2 else 0.0
            delta = max(self.margin_floor, self.z * se)
            gain_vals = (_mean(reports[top].per_world, hold), _mean(inc.per_world, hold))
        cand_s, inc_s = Scored(score=gain_vals[0]), Scored(score=gain_vals[1])
        winner, verdicts = select([(top, cand_s)], inc_s, MinGain(), GateContext(delta=delta))
        if not self.include_incumbent and winner is None:
            winner = top
        idx = 0 if winner is None else int(winner)
        reason = verdicts[0][1].reason if verdicts else ""
        return SelectionResult(idx, ("deploy " if idx else "keep incumbent: ") + f"{reason} (held-out worlds {hold})",
                               dev_vals, {"delta": delta, "top_dev": top, "holdout": hold, "dev": dev,
                                          "holdout_gain": gain_vals[0] - gain_vals[1], "diffs": diffs})
