"""GEPA search state (``gepa/core/state.py:GEPAState``) with full, deterministic resume.

Candidates are :class:`rsi.core.Artifact` objects (content-addressed, stored in an
:class:`rsi.core.ArtifactStore`); the lineage DAG, per-instance validation scores,
frontier, round-robin pointers, rollout counters (by phase) and the per-iteration
trace are kept here and written as ``state.json``. Unlike the reference (weakness 15:
unseeded iteration ids, RNG and sampler state not persisted) the RNG state, the batch
sampler, the merge scheduler and the selector state are persisted too, and iteration
ids are seed-derived, so a killed run resumes onto exactly the uninterrupted
trajectory.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..core.artifact import Artifact
from ..core.ledger import ArtifactStore
from .frontier import FrontierTracker

PHASES = ("seed_val", "minibatch_parent", "minibatch_child", "val_reflective", "merge_subsample", "val_merge")


@dataclass
class RolloutCounter:
    """Metric calls (one (candidate, example) evaluation = one rollout), by phase."""

    by_phase: dict = field(default_factory=lambda: {p: 0 for p in PHASES})

    @property
    def total(self) -> int:
        return int(sum(self.by_phase.values()))

    def add(self, phase: str, n: int) -> None:
        self.by_phase[phase] = self.by_phase.get(phase, 0) + int(n)

    def shares(self) -> dict:
        t = max(self.total, 1)
        return {k: v / t for k, v in self.by_phase.items()}


class SearchState:
    """The candidate pool, its lineage and everything needed to resume."""

    def __init__(self, components: list[str], frontier_type: str = "instance") -> None:
        self.components = list(components)
        self.candidates: list[Artifact] = []
        self.parents: list[list[Optional[int]]] = []
        self.val_scores: list[dict[str, float]] = []
        self.obj_scores: list[Optional[dict]] = []
        self.rr: list[int] = []
        self.kinds: list[str] = []
        self.discovery_evals: list[int] = []
        self.discovery_iter: list[int] = []
        self.frontier = FrontierTracker(frontier_type)
        self.counter = RolloutCounter()
        self.i = -1
        self.trace: list[dict] = []
        self.n_proposals = 0            # reflective children proposed (evaluated on the minibatch)
        self.n_reflection_calls = 0
        self.best_history: list[tuple[int, int]] = []   # (rollouts, best idx) after every change
        self.extra: dict = {}           # rng / sampler / merge / selector / usage snapshots

    # --------------------------------------------------------------- pool ops --
    def agg_scores(self) -> list[float]:
        return [sum(v.values()) / len(v) if v else float("-inf") for v in self.val_scores]

    def add_candidate(self, cand: Artifact, parents: list[Optional[int]], val_scores: dict[str, float],
                      obj_scores: Optional[dict], kind: str, evals_before: int) -> tuple[int, dict]:
        idx = len(self.candidates)
        self.candidates.append(cand)
        self.parents.append(list(parents))
        rr = max((self.rr[p] for p in parents if p is not None), default=0)
        self.rr.append(rr)
        self.val_scores.append(dict(val_scores))
        self.obj_scores.append(obj_scores)
        self.kinds.append(kind)
        self.discovery_evals.append(evals_before)
        self.discovery_iter.append(self.i)
        delta = self.frontier.update(idx, val_scores, obj_scores)
        self.best_history.append((self.counter.total, self.best_idx()))
        return idx, delta

    def best_idx(self) -> int:
        """argmax mean validation score; ties -> higher coverage, then lowest index
        (``FullEvaluationPolicy.get_best_program``)."""
        best, best_s, best_cov = -1, float("-inf"), -1
        for k, sc in enumerate(self.val_scores):
            cov = len(sc)
            avg = sum(sc.values()) / cov if cov else float("-inf")
            if avg > best_s or (avg == best_s and cov > best_cov):
                best, best_s, best_cov = k, avg, cov
        return best

    def depth(self, idx: int) -> int:
        d, cur = 0, idx
        while self.parents[cur] and self.parents[cur][0] is not None:
            cur = self.parents[cur][0]
            d += 1
        return d

    # ------------------------------------------------------------ persistence --
    def to_json(self, store: Optional[ArtifactStore] = None) -> dict:
        if store is not None:
            for c in self.candidates:
                store.put(c)
        return {
            "version": 1, "components": self.components, "i": self.i,
            "candidates": [c.id for c in self.candidates] if store is not None else [c.to_json() for c in
                                                                                     self.candidates],
            "parents": self.parents, "val_scores": self.val_scores, "obj_scores": self.obj_scores, "rr": self.rr,
            "kinds": self.kinds, "discovery_evals": self.discovery_evals, "discovery_iter": self.discovery_iter,
            "frontier": self.frontier.to_json(), "rollouts_by_phase": self.counter.by_phase, "trace": self.trace,
            "n_proposals": self.n_proposals, "n_reflection_calls": self.n_reflection_calls,
            "best_history": self.best_history, "extra": self.extra,
        }

    @classmethod
    def from_json(cls, d: dict, store: Optional[ArtifactStore] = None) -> "SearchState":
        st = cls(d["components"], d["frontier"]["frontier_type"])
        st.i = int(d["i"])
        st.candidates = [store.get(x) if isinstance(x, str) else Artifact.from_json(x) for x in d["candidates"]]
        st.parents = [list(p) for p in d["parents"]]
        st.val_scores = [dict(v) for v in d["val_scores"]]
        st.obj_scores = list(d["obj_scores"])
        st.rr = list(d["rr"])
        st.kinds = list(d["kinds"])
        st.discovery_evals = list(d["discovery_evals"])
        st.discovery_iter = list(d["discovery_iter"])
        st.frontier = FrontierTracker.from_json(d["frontier"])
        st.counter = RolloutCounter(dict(d["rollouts_by_phase"]))
        st.trace = list(d["trace"])
        st.n_proposals = int(d["n_proposals"])
        st.n_reflection_calls = int(d["n_reflection_calls"])
        st.best_history = [tuple(x) for x in d["best_history"]]
        st.extra = dict(d["extra"])
        return st

    def save(self, run_dir: Path, store: ArtifactStore) -> None:
        run_dir.mkdir(parents=True, exist_ok=True)
        tmp = run_dir / "state.json.tmp"
        tmp.write_text(json.dumps(self.to_json(store), default=_jsonable))
        os.replace(tmp, run_dir / "state.json")

    @classmethod
    def load(cls, run_dir: Path, store: ArtifactStore) -> Optional["SearchState"]:
        p = Path(run_dir) / "state.json"
        if not p.exists():
            return None
        return cls.from_json(json.loads(p.read_text()), store)

    # --------------------------------------------------------------- exports --
    def candidates_table(self) -> list[dict]:
        agg = self.agg_scores()
        front = self.frontier.mapping()
        wins = {k: sum(1 for v in front.values() if k in v) for k in range(len(self.candidates))}
        return [{"idx": k, "artifact_id": c.id, "parents": self.parents[k], "kind": self.kinds[k],
                 "val_mean": agg[k], "frontier_wins": wins[k], "rollouts_at_discovery": self.discovery_evals[k],
                 "iteration": self.discovery_iter[k], "depth": self.depth(k)} for k, c in enumerate(self.candidates)]


def _jsonable(x):
    if isinstance(x, (set, frozenset)):
        return sorted(x)
    if isinstance(x, tuple):
        return list(x)
    return repr(x)
