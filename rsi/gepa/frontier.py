"""Per-instance Pareto frontier and the Pareto candidate-selection rule.

Faithful ports of ``gepa/gepa_utils.py`` (``is_dominated``,
``remove_dominated_programs``, ``find_dominator_programs``,
``select_program_candidate_from_pareto_front``) and of the frontier bookkeeping in
``gepa/core/state.py`` (``frontier_type`` = instance | objective | hybrid | cartesian).

The domination rule is GEPA's *set-cover* rule: a program ``y`` is dominated when
every frontier key it wins is also won by some other surviving program; programs are
examined in ascending order of aggregate score, so a low-aggregate generalist can be
pruned in favour of higher-aggregate specialists (spec 3, [run:pareto-probe]).

Only iteration order differs from the reference: members of a front are visited in
sorted order (Python set order of ints is not part of any contract), which makes
sampling reproducible across Python builds.

The module is generic: any loop with a pool of candidates scored per instance can use
:class:`FrontierTracker` + :func:`select_from_pareto_front` as a diversity-preserving
parent selector.
"""
from __future__ import annotations

import random
from typing import Hashable, Mapping, Optional, Sequence

FRONTIER_TYPES = ("instance", "objective", "hybrid", "cartesian")


def is_dominated(y: int, programs: set[int], front_map: Mapping[Hashable, set[int]]) -> bool:
    for front in front_map.values():
        if y not in front:
            continue
        if not any(other in programs for other in sorted(front)):
            return False
    return True


def remove_dominated_programs(front_map: Mapping[Hashable, set[int]],
                              scores: Optional[Mapping[int, float] | Sequence[float]] = None) -> dict:
    """Prune dominated programs from every front (reference semantics)."""
    freq: dict[int, int] = {}
    for key in front_map:
        for p in sorted(front_map[key]):
            freq[p] = freq.get(p, 0) + 1
    programs = list(freq)
    if scores is None:
        score_of = {p: 1.0 for p in programs}
    else:
        score_of = {p: float(scores[p]) for p in programs}
    programs = sorted(programs, key=lambda x: score_of[x])
    dominated: set[int] = set()
    found = True
    while found:
        found = False
        for y in programs:
            if y in dominated:
                continue
            if is_dominated(y, set(programs) - {y} - dominated, front_map):
                dominated.add(y)
                found = True
                break
    dominators = [p for p in programs if p not in dominated]
    out = {k: {p for p in front if p in dominators} for k, front in front_map.items()}
    for k, front in front_map.items():
        if front:
            assert out[k], "pruning emptied a non-empty front"
    return out


def find_dominator_programs(front_map: Mapping[Hashable, set[int]], scores) -> list[int]:
    """Programs that survive pruning (merge candidates), sorted."""
    pruned = remove_dominated_programs(front_map, scores)
    return sorted({p for front in pruned.values() for p in front})


def pareto_frequencies(front_map: Mapping[Hashable, set[int]], scores) -> dict[int, int]:
    pruned = remove_dominated_programs(front_map, scores)
    freq: dict[int, int] = {}
    for key in front_map:
        for p in sorted(pruned[key]):
            freq[p] = freq.get(p, 0) + 1
    return freq


def select_from_pareto_front(front_map: Mapping[Hashable, set[int]], scores, rng: random.Random) -> int:
    """GEPA Algorithm 2: prune, then sample proportionally to frontier frequency."""
    freq = pareto_frequencies(front_map, scores)
    sampling = [p for p, f in freq.items() for _ in range(f)]
    assert sampling, "empty Pareto front"
    return rng.choice(sampling)


class FrontierTracker:
    """Best score and winning programs per frontier key.

    Keys: ``instance`` -> one per validation id; ``objective`` -> one per objective
    (candidate's mean over validation); ``hybrid`` -> both; ``cartesian`` -> one per
    (validation id, objective). ``>`` replaces the winners, ``==`` joins them. Without
    objective scores ``hybrid`` and ``cartesian`` fall back to instance keys (the
    ``optimize_anything`` behaviour); ``objective`` raises ``ValueError``.
    """

    def __init__(self, frontier_type: str = "instance") -> None:
        if frontier_type not in FRONTIER_TYPES:
            raise ValueError(f"frontier_type must be one of {FRONTIER_TYPES}")
        self.frontier_type = frontier_type
        self.best: dict[str, float] = {}
        self.progs: dict[str, set[int]] = {}

    def _keys(self, val_scores: Mapping[str, float], obj_scores: Optional[Mapping[str, Mapping[str, float]]]):
        out: list[tuple[str, float]] = []
        ft = self.frontier_type
        if ft in ("instance", "hybrid"):
            out += [(f"i:{vid}", float(s)) for vid, s in val_scores.items()]
        if ft in ("objective", "hybrid") and obj_scores:
            names = sorted({n for d in obj_scores.values() for n in d})
            for n in names:
                vals = [d[n] for d in obj_scores.values() if n in d]
                if vals:
                    out.append((f"o:{n}", float(sum(vals) / len(vals))))
        if ft == "cartesian" and obj_scores:
            for vid, d in obj_scores.items():
                for n, v in d.items():
                    out.append((f"c:{vid}|{n}", float(v)))
        if ft == "cartesian" and not obj_scores:
            out += [(f"i:{vid}", float(s)) for vid, s in val_scores.items()]
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
