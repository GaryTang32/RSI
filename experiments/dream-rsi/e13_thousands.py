"""E13: "thousands of candidate policies can then be tested"; replay 2000 mutated policies on 6 recorded worlds.

    python experiments/dream-rsi/e13_thousands.py [--n 2000] [--sandboxed 100]
"""
import time

import numpy as np
from _common import domain_of, parse_args, record_world, save, summ

from rsi.dream import DevContext, Eq1Objective, ParametricMutator, ReplayEvaluator, VersionRecord, template_code

GRID, W = (10, 10), 10  # the paper's Pro grid: 10 workspaces x 11 calls


def worlds(n=6):
    return [record_world(domain_of("synthetic", s), W, GRID, s, trace=False) for s in range(200, 200 + n)]


def candidates(n):
    """The adaptive template with PARAMS moved at random by the offline developer, seeds 0..n-1."""
    inc = VersionRecord(0, template_code("adaptive"))
    mut = ParametricMutator(sigma=0.3, n_random=8, directed=False, beta_rule=False)
    return [mut.revise(DevContext(1, [inc], [], [], "", "eq1", W, first_in_phase=False), seed=i).code for i in range(n)]


def main():
    a = parse_args("E13 thousands of candidates", default_seeds=0, extra=lambda ap: (
        ap.add_argument("--n", type=int, default=2000), ap.add_argument("--sandboxed", type=int, default=100)))
    ws = worlds()
    t0 = time.time()
    codes = candidates(a.n)
    gen_s = time.time() - t0
    ev = ReplayEvaluator(Eq1Objective(), W=W, fallback=GRID, runner="inprocess")
    t1 = time.time()
    vals, disq, eps = [], 0, 0
    for c in codes:
        rep = ev.evaluate(c, ws, sweep=False)
        vals.append(rep.value)
        disq += rep.disqualified
        eps += rep.n_episodes
    wall_in = time.time() - t1
    evs = ReplayEvaluator(Eq1Objective(), W=W, fallback=GRID, runner="subprocess")
    t2 = time.time()
    vals_sb, disq_sb = [], 0
    for c in codes[: a.sandboxed]:
        rep = evs.evaluate(c, ws, sweep=False)
        vals_sb.append(rep.value)
        disq_sb += rep.disqualified
    wall_sb = time.time() - t2
    res = {"n_candidates": a.n, "distinct_codes": len(set(codes)), "worlds": len(ws),
           "world_cells": [w.size for w in ws],
           "generation_s": round(gen_s, 2), "inprocess": {"wall_s": round(wall_in, 1), "episodes": eps,
                                                          "ms_per_episode": 1e3 * wall_in / max(1, eps),
                                                          "disqualified_episodes": disq},
           "subprocess": {"n": a.sandboxed, "wall_s": round(wall_sb, 1),
                          "ms_per_episode": 1e3 * wall_sb / max(1, a.sandboxed * len(ws)),
                          "disqualified_episodes": disq_sb,
                          "same_values_as_inprocess": bool(np.allclose(vals_sb, vals[: a.sandboxed]))},
           "value": summ(vals), "best_of_first_4": max(vals[:4]), "best_of_first_32": max(vals[:32]),
           "best_of_all": max(vals),
           "projected_subprocess_wall_s_for_all": round(wall_sb / max(1, a.sandboxed) * a.n, 1)}
    # preregistered pass: every candidate on every world, no honest episode disqualified, < 20 min
    res["passes"] = bool(res["inprocess"]["episodes"] == a.n * len(ws) and disq == 0 and wall_in < 1200)
    print(res)
    save("e13_thousands", {"config": {"grid": GRID, "W": W, "objective": "Eq.1 normalized (beta1 0.01, beta2 0.005)",
                                      "developer": "ParametricMutator(sigma=0.3, n_random=8, undirected)"},
                           "results": res, "values": vals})


if __name__ == "__main__":
    main()
