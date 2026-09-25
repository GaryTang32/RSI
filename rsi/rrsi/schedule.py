"""Annealed L0 update budget, Eq. (anneal) (mirrors ``rrsi/schedule.py``)::

    b_t = ceil( b_min + (b_max - b_min) * 1/2 * (1 + cos(pi t / T)) ),   t = 0..T-1

Early rounds may bundle several coordinated edits in one candidate; late rounds
become sparse and attributable. The constraint bounds ||z_t||_0, the number of
independent edits in one proposal, and nothing else: the set of mechanisms the
harness may eventually contain is not restricted.

Note (spec section 4.2): with ``ceil`` and ``t <= T-1`` the budget never reaches
``b_min`` inside a run (the last round gets 2 when b_min = 1). The paper text says
it "ends at one", so ``rounding="floor_at_bmin_last"`` is offered as an explicit
alternative: identical to ``ceil`` except that the final round t = T-1 gets b_min.
"""
from __future__ import annotations

import math


def edit_budget(t: int, T: int, b_min: int, b_max: int, rounding: str = "ceil") -> int:
    """b_t for round t (0-indexed) of a T-round run."""
    if T <= 0:
        return int(b_max)
    t = max(0, min(int(t), int(T)))
    if rounding == "floor_at_bmin_last" and t >= T - 1:
        return int(b_min)
    v = b_min + (b_max - b_min) * 0.5 * (1.0 + math.cos(math.pi * t / T))
    # Guard against 1.0000000002 -> 2 from floating error at t = T.
    return int(math.ceil(round(v, 9)))


def budget_table(T: int, b_min: int, b_max: int, rounding: str = "ceil") -> list[int]:
    return [edit_budget(t, T, b_min, b_max, rounding) for t in range(T)]
