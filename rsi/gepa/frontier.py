"""Per-instance Pareto frontier and set-cover pruning (ports of ``gepa/gepa_utils.py``, fronts visited sorted)."""
from __future__ import annotations

import random
from typing import Hashable, Mapping, Optional, Sequence

FRONTIER_TYPES = ("instance", "objective", "hybrid", "cartesian")


def is_dominated(y: int, programs: set[int], front_map: Mapping[Hashable, set[int]]) -> bool:
    """True when every front ``y`` wins is also won by some program in ``programs``."""
    return all(any(p in programs for p in front) for front in front_map.values() if y in front)


def _frequencies(front_map: Mapping[Hashable, set[int]], fronts: Mapping[Hashable, set[int]]) -> dict[int, int]:
    freq: dict[int, int] = {}
    for key in front_map:
        for p in sorted(fronts[key]):
            freq[p] = freq.get(p, 0) + 1
    return freq


def remove_dominated_programs(front_map: Mapping[Hashable, set[int]],
                              scores: Optional[Mapping[int, float] | Sequence[float]] = None) -> dict:
    """Prune dominated programs from every front, lowest aggregate score first."""
    programs = list(_frequencies(front_map, front_map))
    score_of = {p: 1.0 if scores is None else float(scores[p]) for p in programs}
    programs.sort(key=lambda x: score_of[x])
    dominated: set[int] = set()
    found = True
    while found:
        found = False
        for y in programs:
            if y not in dominated and is_dominated(y, set(programs) - {y} - dominated, front_map):
                dominated.add(y)
                found = True
                break
    out = {k: {p for p in front if p not in dominated} for k, front in front_map.items()}
    for k, front in front_map.items():
        if front:
            assert out[k], "pruning emptied a non-empty front"
    return out


def find_dominator_programs(front_map: Mapping[Hashable, set[int]], scores) -> list[int]:
    """Programs that survive pruning (merge candidates), sorted."""
    return sorted({p for front in remove_dominated_programs(front_map, scores).values() for p in front})


def pareto_frequencies(front_map: Mapping[Hashable, set[int]], scores) -> dict[int, int]:
    return _frequencies(front_map, remove_dominated_programs(front_map, scores))


def select_from_pareto_front(front_map: Mapping[Hashable, set[int]], scores, rng: random.Random) -> int:
    """GEPA Algorithm 2: prune, then sample proportionally to frontier frequency."""
    sampling = [p for p, f in pareto_frequencies(front_map, scores).items() for _ in range(f)]
    assert sampling, "empty Pareto front"
    return rng.choice(sampling)


class FrontierTracker:
    """Best score and winners per frontier key; without objective scores hybrid/cartesian use instance keys."""

    def __init__(self, frontier_type: str = "instance") -> None:
        if frontier_type not in FRONTIER_TYPES:
            raise ValueError(f"frontier_type must be one of {FRONTIER_TYPES}")
        self.frontier_type = frontier_type
        self.best: dict[str, float] = {}
        self.progs: dict[str, set[int]] = {}

    def _keys(self, val_scores: Mapping[str, float],
              obj_scores: Optional[Mapping[str, Mapping[str, float]]]) -> list[tuple[str, float]]:
        ft = self.frontier_type
        out = []
        if ft in ("instance", "hybrid") or (ft == "cartesian" and not obj_scores):
            out += [(f"i:{vid}", float(s)) for vid, s in val_scores.items()]
        if ft in ("objective", "hybrid") and obj_scores:
            for n in sorted({n for d in obj_scores.values() for n in d}):
                vals = [d[n] for d in obj_scores.values() if n in d]
                out.append((f"o:{n}", float(sum(vals) / len(vals))))
        if ft == "cartesian" and obj_scores:
            out += [(f"c:{vid}|{n}", float(v)) for vid, d in obj_scores.items() for n, v in d.items()]
        return out

    def update(self, idx: int, val_scores: Mapping[str, float],
               obj_scores: Optional[Mapping[str, Mapping[str, float]]] = None) -> dict:
        """Add program ``idx``; returns ``{"won": [...], "tied": [...], "displaced": {key: [...]}}``."""
        delta = {"won": [], "tied": [], "displaced": {}}
        if self.frontier_type == "objective" and not any(obj_scores.values() if obj_scores else ()):
            raise ValueError("frontier_type='objective' needs objective scores from the evaluator "
                             "(Trial.meta['objectives'] or DomainAdapter(objective_fn=...))")
        for key, s in self._keys(val_scores, obj_scores):
            prev = self.best.get(key)
            if prev is None or s > prev:
                if self.progs.get(key):
                    delta["displaced"][key] = sorted(self.progs[key])
                self.best[key] = s
                self.progs[key] = {idx}
                delta["won"].append(key)
            elif s == prev:
                self.progs[key].add(idx)
                delta["tied"].append(key)
        return delta

    def mapping(self) -> dict[str, set[int]]:
        return {k: set(v) for k, v in self.progs.items()}

    def members(self) -> list[int]:
        return sorted({p for v in self.progs.values() for p in v})

    def to_json(self) -> dict:
        return {"frontier_type": self.frontier_type, "best": self.best,
                "progs": {k: sorted(v) for k, v in self.progs.items()}}

    @classmethod
    def from_json(cls, d: dict) -> "FrontierTracker":
        f = cls(d["frontier_type"])
        f.best = {k: float(v) for k, v in d["best"].items()}
        f.progs = {k: set(v) for k, v in d["progs"].items()}
        return f
