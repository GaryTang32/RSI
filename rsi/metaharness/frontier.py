"""Pareto frontier (score up, cost down), per-unit bests and 2-D hypervolume."""
from __future__ import annotations

from typing import Iterable, Sequence


def dominates(a: tuple[float, float], b: tuple[float, float]) -> bool:
    """(score, cost): a dominates b."""
    return a[0] >= b[0] and a[1] <= b[1] and (a[0] > b[0] or a[1] < b[1])


def pareto_frontier(points: Iterable[tuple[str, float, float]]) -> list[tuple[str, float, float]]:
    """Non-dominated ``(name, score, cost)`` points sorted by ``(-score, cost)``; exact ties keep input order."""
    pts = list(points)
    front = [p for p in pts if not any(dominates(o[1:], p[1:]) for o in pts)]
    return sorted(front, key=lambda x: (-x[1], x[2]))


def per_unit_best(per_unit: dict[str, dict[str, tuple[float, float]]]) -> dict[str, dict]:
    """``per_unit[system][unit] = (score, cost)`` -> ``{unit: {best_system, score, cost}}``; ties go to the first."""
    out = {}
    for u in sorted({u for d in per_unit.values() for u in d}):
        cands = [(s, d[u][0], d[u][1]) for s, d in per_unit.items() if u in d]
        s, sc, c = max(cands, key=lambda x: (x[1], -x[2]))
        out[u] = {"best_system": s, "score": sc, "cost": c}
    return out


def hypervolume(points: Sequence[tuple[str, float, float]], ref_cost: float, ref_score: float = 0.0) -> float:
    """Area dominated by ``points`` relative to ``(ref_score, ref_cost)``; points beyond it are ignored."""
    front = [(c, s) for _, s, c in pareto_frontier(points) if c <= ref_cost and s > ref_score]
    area, last = 0.0, ref_score
    for c, s in sorted(front):
        if s > last:
            area += (ref_cost - c) * (s - last)
            last = s
    return float(area)
