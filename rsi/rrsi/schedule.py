"""Annealed L0 edit budget b_t = ceil(b_min + (b_max - b_min) * (1 + cos(pi t / T)) / 2), Eq. (anneal)."""
from __future__ import annotations

import math


def edit_budget(t: int, T: int, b_min: int, b_max: int, rounding: str = "ceil") -> int:
    """b_t for round t (0-indexed); ``"floor_at_bmin_last"`` gives b_min at t = T-1 (``ceil`` never reaches it)."""
    if T <= 0:
        return int(b_max)
    t = max(0, min(int(t), int(T)))
    if rounding == "floor_at_bmin_last" and t >= T - 1:
        return int(b_min)
    v = b_min + (b_max - b_min) * 0.5 * (1.0 + math.cos(math.pi * t / T))
    return int(math.ceil(round(v, 9)))  # round first: 1.0000000002 must not become 2


def budget_table(T: int, b_min: int, b_max: int, rounding: str = "ceil") -> list[int]:
    return [edit_budget(t, T, b_min, b_max, rounding) for t in range(T)]
