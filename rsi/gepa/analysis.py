"""Analysis of GEPA runs: search-tree shape, best-so-far curves in rollouts, minibatch-gate error rates."""
from __future__ import annotations

from typing import Callable, Optional, Sequence

import numpy as np

from ..core.artifact import Artifact


def tree_metrics(state) -> dict:
    """Depth, branching, lineages, and how often the selected parent was the current best."""
    n = len(state.candidates)
    children: dict[int, list[int]] = {k: [] for k in range(n)}
    for k, ps in enumerate(state.parents):
        for p in ps:
            if p is not None:
                children[p].append(k)
    depths = [state.depth(k) for k in range(n)]
    internal = [k for k in range(n) if children[k]]
    sel = top = prev_best = 0
    for e in state.trace:
        if "selected_program_candidate" in e:
            sel += 1
            top += int(e["selected_program_candidate"] == prev_best)
        prev_best = e.get("best_idx", prev_best)
    parents_used = {e["selected_program_candidate"] for e in state.trace if "selected_program_candidate" in e}
    return {
        "n_candidates": n, "max_depth": max(depths) if depths else 0,
        "mean_depth": float(np.mean(depths)) if depths else 0.0,
        "mean_branching": float(np.mean([len(children[k]) for k in internal])) if internal else 0.0,
        "n_leaves": n - len(internal), "root_children": len(children.get(0, [])),
        "frac_selected_top": top / sel if sel else float("nan"), "distinct_parents": len(parents_used),
        "frontier_size": len(state.frontier.members()),
    }


def gepa_curve(result, truth: Callable[[Artifact], float]) -> list[tuple[int, float]]:
    """(rollouts, truth(best candidate)) after every change of the pool."""
    st = result.state
    cache: dict[int, float] = {}
    for _, b in st.best_history:
        if b not in cache:
            cache[b] = truth(st.candidates[b])
    return [(int(r), cache[b]) for r, b in st.best_history]


def trajectory_curve(result, truth: Callable[[Artifact], float]) -> list[tuple[int, float]]:
    """Curve for baselines whose trajectory rows carry ``rollouts`` + ``best_id`` and ``result.artifacts``."""
    arts = getattr(result, "artifacts", {}) or {}
    cache: dict[str, float] = {}
    for row in result.trajectory:
        bid = row["best_id"]
        if bid not in cache:
            cache[bid] = truth(arts.get(bid) or (result.best if result.best.id == bid else result.baseline))
    return [(int(row["rollouts"]), cache[row["best_id"]]) for row in result.trajectory]


def curve_at(curve: Sequence[tuple[int, float]], budgets: Sequence[int]) -> list[float]:
    """Step-function value of a best-so-far curve at each budget (nan before the first point)."""
    out = []
    for b in budgets:
        vals = [v for r, v in curve if r <= b]
        out.append(vals[-1] if vals else float("nan"))
    return out


def rollouts_to_target(curve: Sequence[tuple[int, float]], target: float) -> Optional[int]:
    return next((int(r) for r, v in curve if v >= target), None)


def gate_errors(events: Sequence[dict]) -> dict:
    """False-accept / false-reject rates of the minibatch gate from ``{"accepted", "true_gain"}`` events."""
    acc = [e for e in events if e["accepted"]]
    rej = [e for e in events if not e["accepted"]]
    fa = sum(1 for e in acc if e["true_gain"] <= 0)
    fr = sum(1 for e in rej if e["true_gain"] > 0)
    return {"n": len(events), "n_accepted": len(acc), "false_accept_rate": fa / len(acc) if acc else float("nan"),
            "false_reject_rate": fr / len(rej) if rej else float("nan"),
            "accept_rate": len(acc) / len(events) if events else float("nan")}
