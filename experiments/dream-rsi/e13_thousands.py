"""E13 - "Thousands of candidate policies can then be tested" (claims audit L3, retry round 2).

Fig. 1-2 captions: the history "dreams up a massive pool of alternative policies"; "thousands of candidate
policies can then be tested". Our dreaming phases use M = 4-6 versions (E9 goes to 32). This measures the
claim directly: 2000 candidate policies (the adaptive template with PARAMS moved by the offline developer,
``ParametricMutator`` with random moves, seeds 0-1999) are replayed on 6 worlds recorded by parallel refine on
the paper's Pro grid (10 workspaces x 11 calls, W = 10), in one process; 100 of them also in the subprocess
sandbox (where LLM-written policies run). Preregistered pass: all 2000 evaluated on all 6 worlds, no honest
episode disqualified, < 20 min wall. Also reported: the best replay value among the first M candidates vs
among all 2000 (what a massive pool buys on the dev worlds; E9 measures what it costs on fresh worlds).

    python experiments/dream-rsi/e13_thousands.py [--n 2000] [--sandboxed 100]
"""
import time

import numpy as np

from _common import domain_of, parse_args, save, summ

from rsi.dream import (Config, DevContext, DreamRSILoop, Eq1Objective, ParametricMutator, ReplayEvaluator,
                       VersionRecord, template_code)

GRID, W = (10, 10), 10


def worlds(n=6):
    out = []
    for s in range(200, 200 + n):
        dom = domain_of("synthetic", s)
        cfg = Config(rounds=1, W=W, branch_count=GRID[0], refine_count=GRID[1], dream=False, sandbox="inprocess",
                     seed=s, agent_workers=1, trace=False)
        out.append(DreamRSILoop(dom.as_task(), dom.mock_agent(), config=cfg).run().meta["worlds"][0])
    return out


def candidates(n):
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
    distinct = len(set(codes))
    res = {"n_candidates": a.n, "distinct_codes": distinct, "worlds": len(ws), "world_cells": [w.size for w in ws],
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
    res["passes"] = bool(res["inprocess"]["episodes"] == a.n * len(ws) and disq == 0 and wall_in < 1200)
    print(res)
    save("e13_thousands", {"config": {"grid": GRID, "W": W, "objective": "Eq.1 normalized (beta1 0.01, beta2 0.005)",
                                      "developer": "ParametricMutator(sigma=0.3, n_random=8, undirected)"},
                           "results": res, "values": vals})


if __name__ == "__main__":
    main()
