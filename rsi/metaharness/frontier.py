"""Pareto frontier and per-unit bests (``benchmark.py:compute_pareto_frontier`` / ``print_frontier``).

A point is ``(name, score, cost)``; it is on the frontier iff no other point has
``score' >= score and cost' <= cost`` with at least one strict inequality. The
frontier is sorted by ``(-score, cost)``, so ``frontier[0]`` is the loop's "best"
(the highest-accuracy Pareto point, ``frontier._pareto[0].val_accuracy``).
Per unit (dataset / task), ``best = argmax (score, -cost)``.
"""
from __future__ import annotations

from typing import Iterable, Optional, Sequence


def pareto_frontier(points: Iterable[tuple[str, float, float]]) -> list[tuple[str, float, float]]:
    pts = list(points)
    front = [p for p in pts
             if not any(o[1] >= p[1] and o[2] <= p[2] and (o[1] > p[1] or o[2] < p[2]) for o in pts)]
    return sorted(front, key=lambda x: (-x[1], x[2], x[0]))


def per_unit_best(per_unit: dict[str, dict[str, tuple[float, float]]]) -> dict[str, dict]:
    """``per_unit[system][unit] = (score, cost)`` -> ``{unit: {best_system, score, cost}}``."""
    units = sorted({u for d in per_unit.values() for u in d})
    out = {}
    for u in units:
        cands = [(s, d[u][0], d[u][1]) for s, d in per_unit.items() if u in d]
        if cands:
            s, sc, c = max(cands, key=lambda x: (x[1], -x[2], x[0]))
            out[u] = {"best_system": s, "score": sc, "cost": c}
    return out


def hypervolume(points: Sequence[tuple[str, float, float]], ref_cost: float, ref_score: float = 0.0) -> float:
    """2-D hypervolume (score up, cost down) dominated by ``points`` relative to the
    reference point ``(ref_score, ref_cost)``. Points beyond the reference are ignored.

    With the front sorted by cost ascending (so score ascending), the union of the
    dominated rectangles is ``sum_i (ref_cost - c_i) * (s_i - s_{i-1})``, ``s_0 = ref_score``."""
    front = [(c, s) for _, s, c in pareto_frontier(points) if c <= ref_cost and s > ref_score]
    area, last = 0.0, ref_score
    for c, s in sorted(front):
        if s > last:
            area += (ref_cost - c) * (s - last)
            last = s
    return float(area)


def dominates(a: tuple[float, float], b: tuple[float, float]) -> bool:
    """(score, cost): a dominates b."""
    return a[0] >= b[0] and a[1] <= b[1] and (a[0] > b[0] or a[1] < b[1])


def best_point(front: Sequence[tuple[str, float, float]]) -> Optional[tuple[str, float, float]]:
    return front[0] if front else None
