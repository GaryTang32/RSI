"""System-aware merge (GEPA Algorithms 3-4, ``gepa/proposer/merge.py``).

Given two Pareto-surviving candidates i, j that share an ancestor a (neither an
ancestor of the other; agg[a] <= agg[i], agg[j]; and at least one module where one
side still equals the ancestor while the sides differ), build a child module-wise:

* a == i != j  -> take j's text (the side that changed the module);
* a == j != i  -> take i's text;
* both changed -> take the text of the higher-aggregate side (ties -> rng);
* unchanged    -> keep it.

The child is scored on a 5-id validation subsample (up to ceil(5/3) ids each from
"i better", "j better", "tie") and accepted by the engine iff its subsample sum is
>= the better parent's. Scheduling follows the reference engine: one merge becomes
*due* after each accepted reflective child while ``total_merges_tested <
max_merge_invocations``; a merge is attempted only in the iteration right after an
accepted reflective child.

Terms. A merge *check* is one call of :meth:`MergeProposer.propose` (the reference sets
``invoked_merge`` in the run log even when no valid triplet exists; such a check costs
no rollout and the iteration falls through to reflection). A merge *invocation* is a
check that found a valid (i, j, a) triplet, built the merged child and scored it on its
subsample - the paper's "invoking merge when identified" [App. D.1]; it is counted by
:attr:`MergeProposer.n_invocations` (= ``len(merges_performed[0])``), accepted or not.

Cap modes (``cap_mode``):

* ``"reference_soft"`` (default) - ``gepa-ai/gepa@d771eb21`` exactly: the reference's
  ``total_merges_tested`` counts *accepted* merges only (``core/engine.py:1038-1039``)
  and ``max_merge_invocations`` is checked only when ``merges_due`` is incremented
  (``core/engine.py:702``). Due merges accumulate while no triplet exists, so accepted
  merges can exceed the cap, and rejected merges consume neither counter, so the number
  of invocations is unbounded;
* ``"hard"`` - the paper's "merge is invoked a maximum of 5 times" [App. G.4] and the
  reference's own parameter doc ("The maximum number of merge invocations to
  perform", ``api.py``): no check is made once ``n_invocations >=
  max_merge_invocations``, so at most that many merged children are ever built and
  scored (accepted + rejected <= cap);
* ``"accepted"`` - caps *accepted* merges at attempt time (``total_merges_tested <
  max_merge_invocations``). This was this repo's ``"hard"`` mode before the claim audit;
  it is neither the paper's nor the reference's semantics (rejected merges are
  unlimited) and is kept only to reproduce earlier E4 numbers.

Deviations: ancestors, modules and pair candidates are visited in sorted order
(reproducible across Python builds); when every eligible ancestor has aggregate 0 the
reference raises ``ValueError`` from ``random.choices`` - here ``zero_weight="uniform"``
(default) falls back to uniform weights, ``"raise"`` reproduces the crash.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Callable, Optional, Sequence

from ..core.artifact import Artifact
from .frontier import find_dominator_programs

CAP_MODES = ("reference_soft", "hard", "accepted")


def get_ancestors(parents: Sequence[Sequence[Optional[int]]], node: int) -> set[int]:
    found: set[int] = set()
    stack = [node]
    while stack:
        for p in parents[stack.pop()]:
            if p is not None and p not in found:
                found.add(p)
                stack.append(p)
    return found


def desirable(cands: Sequence[Artifact], components: Sequence[str], a: int, i: int, j: int) -> bool:
    for m in components:
        pa, pi, pj = cands[a].get(m), cands[i].get(m), cands[j].get(m)
        if (pa == pi or pa == pj) and pi != pj:
            return True
    return False


def filter_ancestors(i, j, common, merges_performed, agg, cands, components) -> list[int]:
    out = []
    for a in sorted(common):
        if (i, j, a) in merges_performed[0]:
            continue
        if agg[a] > agg[i] or agg[a] > agg[j]:
            continue
        if not desirable(cands, components, a, i, j):
            continue
        out.append(a)
    return out


def _choose_weighted(rng: random.Random, items: list[int], weights: list[float], zero_weight: str) -> int:
    if sum(weights) <= 0:
        if zero_weight == "raise":
            raise ValueError("Total of weights must be greater than zero")
        weights = [1.0] * len(items)
    return rng.choices(items, k=1, weights=weights)[0]


def find_common_ancestor_pair(rng, parents, program_indexes, merges_performed, agg, cands, components,
                              max_attempts: int = 10, zero_weight: str = "uniform"):
    for _ in range(max_attempts):
        if len(program_indexes) < 2:
            return None
        i, j = rng.sample(list(program_indexes), 2)
        if i == j:
            continue
        if j < i:
            i, j = j, i
        anc_i, anc_j = get_ancestors(parents, i), get_ancestors(parents, j)
        if j in anc_i or i in anc_j:
            continue
        common = filter_ancestors(i, j, anc_i & anc_j, merges_performed, agg, cands, components)
        if common:
            a = _choose_weighted(rng, common, [agg[x] for x in common], zero_weight)
            return i, j, a
    return None


def merge_texts(rng, cands, components, agg, i, j, a) -> tuple[dict[str, str], tuple[int, ...]]:
    """Module-wise crossover; returns (new component texts, source index per module)."""
    new: dict[str, str] = {}
    desc: tuple[int, ...] = ()
    for m in sorted(components):
        pa, pi, pj = cands[a].get(m), cands[i].get(m), cands[j].get(m)
        if (pa == pi or pa == pj) and pi != pj:
            src = j if pa == pi else i
        elif pa != pi and pa != pj:
            src = i if agg[i] > agg[j] else (j if agg[j] > agg[i] else rng.choice([i, j]))
        else:  # pi == pj
            src = i
        new[m] = cands[src].get(m, "")
        desc = (*desc, src)
    return new, desc


def sample_and_attempt_merge(agg, rng, merge_candidates, merges_performed, cands, parents, components,
                             has_overlap: Optional[Callable[[int, int], bool]] = None, max_attempts: int = 10,
                             zero_weight: str = "uniform"):
    """Reference ``sample_and_attempt_merge_programs_by_common_predictors``.
    Returns (merged Artifact, i, j, ancestor, module sources) or None."""
    if len(merge_candidates) < 2 or len(parents) < 3:
        return None
    for _ in range(max_attempts):
        found = find_common_ancestor_pair(rng, parents, merge_candidates, merges_performed, agg, cands, components,
                                          max_attempts, zero_weight)
        if found is None:
            continue
        i, j, a = found
        if (i, j, a) in merges_performed[0]:
            continue
        new, desc = merge_texts(rng, cands, components, agg, i, j, a)
        if (i, j, desc) in merges_performed[1]:
            continue
        if has_overlap is not None and not has_overlap(i, j):
            continue
        merges_performed[1].append((i, j, desc))
        return cands[a].with_files(new), i, j, a, desc
    return None


def select_eval_subsample(rng: random.Random, s1: dict, s2: dict, n: int = 5) -> list[str]:
    """Reference ``select_eval_subsample_for_merged_program`` (sorted id order)."""
    common = sorted(set(s1) & set(s2))
    p1 = [k for k in common if s1[k] > s2[k]]
    p2 = [k for k in common if s2[k] > s1[k]]
    p3 = [k for k in common if k not in p1 and k not in p2]
    n_each = max(1, math.ceil(n / 3))
    selected: list[str] = []
    for bucket in (p1, p2, p3):
        if len(selected) >= n:
            break
        avail = [k for k in bucket if k not in selected]
        take = min(len(avail), n_each, n - len(selected))
        if take > 0:
            selected += rng.sample(avail, k=take)
    remaining = n - len(selected)
    if remaining > 0:
        unused = [k for k in common if k not in selected]
        if len(unused) >= remaining:
            selected += rng.sample(unused, k=remaining)
        elif common:
            selected += rng.choices(common, k=remaining)
    return selected[:n]


@dataclass
class MergeProposal:
    candidate: Artifact
    parents: list[int]
    ancestor: int
    sources: tuple
    subsample_ids: list[str]
    sub_before: list[float]            # [sum over subsample of S_i, of S_j]
    sub_after: list[float]


@dataclass
class MergeProposer:
    """Merge scheduling state + proposal (evaluation is delegated to ``evaluate``)."""

    rng: random.Random
    max_merge_invocations: int = 5
    val_overlap_floor: int = 5
    subsample_size: int = 5
    cap_mode: str = "reference_soft"
    zero_weight: str = "uniform"
    merges_due: int = 0
    total_merges_tested: int = 0
    last_iter_found_new_program: bool = False
    merges_performed: tuple = field(default_factory=lambda: ([], []))
    n_attempts: int = 0
    n_none: int = 0

    def __post_init__(self) -> None:
        if self.val_overlap_floor <= 0:
            raise ValueError("val_overlap_floor should be a positive integer")
        if self.cap_mode not in CAP_MODES:
            raise ValueError(f"cap_mode must be one of {CAP_MODES}")

    @property
    def n_invocations(self) -> int:
        """Merges actually performed (triplet found, child built and scored on its subsample),
        accepted or rejected. Derived from the persisted ``merges_performed`` log, so it
        survives resume."""
        return len(self.merges_performed[0])

    # scheduling (engine calls these at the reference's points)
    def schedule_if_needed(self) -> None:
        self.last_iter_found_new_program = True
        if self.total_merges_tested < self.max_merge_invocations:
            self.merges_due += 1

    def should_attempt(self) -> bool:
        if self.cap_mode == "hard" and self.n_invocations >= self.max_merge_invocations:
            return False            # paper: "invoked a maximum of N times" (accepted + rejected)
        if self.cap_mode == "accepted" and self.total_merges_tested >= self.max_merge_invocations:
            return False            # pre-audit "hard" semantics: caps accepted merges only
        return self.merges_due > 0 and self.last_iter_found_new_program

    def on_accepted(self) -> None:
        self.merges_due -= 1
        self.total_merges_tested += 1

    def propose(self, state, evaluate: Callable[[Artifact, list[str]], list[float]]) -> Optional[MergeProposal]:
        self.n_attempts += 1
        agg = state.agg_scores()
        cands_idx = find_dominator_programs(state.frontier.mapping(), agg)

        def overlap(i, j):
            return len(set(state.val_scores[i]) & set(state.val_scores[j])) >= self.val_overlap_floor

        out = sample_and_attempt_merge(agg, self.rng, cands_idx, self.merges_performed, state.candidates,
                                       state.parents, state.components, overlap, zero_weight=self.zero_weight)
        if out is None:
            self.n_none += 1
            return None
        child, i, j, a, desc = out
        self.merges_performed[0].append((i, j, a))
        sub = select_eval_subsample(self.rng, state.val_scores[i], state.val_scores[j], self.subsample_size)
        if not sub:
            return None
        before = [sum(state.val_scores[i][k] for k in sub), sum(state.val_scores[j][k] for k in sub)]
        after = evaluate(child, sub)
        return MergeProposal(child, [i, j], a, desc, sub, before, list(after))

    def get_state(self) -> dict:
        return {"merges_due": self.merges_due, "total_merges_tested": self.total_merges_tested,
                "n_invocations": self.n_invocations, "cap_mode": self.cap_mode,
                "last_iter_found_new_program": self.last_iter_found_new_program,
                "merges_performed": [[list(x) for x in self.merges_performed[0]],
                                     [[i, j, list(d)] for i, j, d in self.merges_performed[1]]],
                "n_attempts": self.n_attempts, "n_none": self.n_none}

    def set_state(self, d: dict) -> None:
        self.merges_due = int(d["merges_due"])
        self.total_merges_tested = int(d["total_merges_tested"])
        self.last_iter_found_new_program = bool(d["last_iter_found_new_program"])
        a, b = d["merges_performed"]
        self.merges_performed = ([tuple(x) for x in a], [(i, j, tuple(dd)) for i, j, dd in b])
        self.n_attempts = int(d.get("n_attempts", 0))
        self.n_none = int(d.get("n_none", 0))
