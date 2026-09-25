"""E7 - One objective with explicit dials.

Claim [doc "puts quality, spending and parallelism into one explicit objective, with a
dial the strategy tunes across cycles"; paper Eq.1 and App.B.2 beta]: beta1 prices
attempts, beta2 rewards parallel batches, and the policy's own ``beta`` knob traces a
work/attainment trade-off.

1. A pool of candidate policies (adaptive-template variants x beta values + rule
   policies) is replayed once over 8 recorded synthetic worlds; for every (beta1, beta2)
   on a grid we select the argmax of Eq.1 and report the selected policy's probes N,
   rounds k, mean batch size and attainment. Predicted: higher beta1 -> fewer probes;
   higher beta2 -> larger batches / fewer rounds.
2. The adaptive policy's beta sweep (0 .. 1): probes and attainment should rise together
   (a monotone frontier), which is what makes the Listing-2 Pareto reward meaningful.
3. Eq.1 vs the Pareto sweep reward as the selection objective (which candidate each picks).

    python experiments/dream-rsi/e7_objective_dials.py [--quick]
"""
import random

import numpy as np
from _common import figure, parse_args, save, spearman

from rsi.dream import (DreamRSILoop, Config, Eq1Objective, ParetoSweepObjective, ReplayEvaluator, adaptive, code_of,
                       rules)
from rsi.dream.developer import SPACE
from rsi.domains.discovery import SyntheticConfig, SyntheticDomain

W, GRID = 4, (6, 4)


def record(seed):
    dom = SyntheticDomain(SyntheticConfig(seed=seed))
    cfg = Config(rounds=1, W=W, branch_count=GRID[0], refine_count=GRID[1], dream=False, sandbox="inprocess",
                 seed=seed, agent_workers=1)
    return DreamRSILoop(dom.as_task(), dom.mock_agent(), config=cfg).run().meta["worlds"][0]


def candidates(n_random):
    rng = random.Random(0)
    pool = {}
    for i in range(n_random):
        p = {}
        for k, (lo, hi, is_int) in SPACE.items():
            v = rng.uniform(lo, hi)
            p[k] = int(round(v)) if is_int else round(v, 3)
        p["default_beta"] = round(rng.uniform(0, 1), 2)
        pool[f"adaptive_r{i}"] = code_of(adaptive(**p))
    for b in (0.0, 0.25, 0.5, 0.75, 1.0):
        pool[f"adaptive_b{b}"] = code_of(adaptive(default_beta=b))
    for cap in (1, 2, 3, 4):
        pool[f"refine_cap{cap}"] = code_of(rules(cap=cap))
        pool[f"gainstop_cap{cap}"] = code_of(rules(cap=cap, gain_stop=0.02))
    for r in (1, 2, 3):
        pool[f"stop_after_{r}"] = code_of(rules(max_rounds=r))
    return pool


def main():
    a = parse_args("E7 objective dials", default_seeds=8)
    worlds = [record(200 + i) for i in range(a.seeds)]
    pool = candidates(12 if a.quick else 40)
    ev = ReplayEvaluator(Eq1Objective(), W=W, fallback=GRID, runner="inprocess")
    eps = {name: ev.evaluate(code, worlds).episodes for name, code in pool.items()}
    stats = {n: {"N": float(np.mean([e.N for e in es])), "k": float(np.mean([e.k for e in es])),
                 "batch": float(np.mean([e.mean_batch for e in es])),
                 "attainment": float(np.mean([e.attainment for e in es]))} for n, es in eps.items()}
    b1s = [0.0, 0.0025, 0.005, 0.01, 0.02, 0.04]
    b2s = [0.0, 0.005, 0.02, 0.05, 0.1, 0.2, 0.4]
    grid = []
    for b1 in b1s:
        for b2 in b2s:
            obj = Eq1Objective(beta1=b1, beta2=b2)
            vals = {n: obj.score(es) for n, es in eps.items()}
            win = max(vals, key=lambda n: (vals[n], n))
            grid.append({"beta1": b1, "beta2": b2, "selected": win, "V": vals[win], **stats[win]})
    rho_b1_N = spearman([g["beta1"] for g in grid], [g["N"] for g in grid])
    rho_b2_batch = spearman([g["beta2"] for g in grid], [g["batch"] for g in grid])
    rho_b2_k = spearman([g["beta2"] for g in grid], [g["k"] for g in grid])
    print(f"selected policy vs dials: rho(beta1, N) = {rho_b1_N:+.2f}; rho(beta2, batch) = {rho_b2_batch:+.2f}; "
          f"rho(beta2, rounds) = {rho_b2_k:+.2f}")
    for g in grid:
        if g["beta2"] in (0.0, 0.05) and g["beta1"] in (0.0, 0.01, 0.04):
            print(f"   beta1={g['beta1']:<6} beta2={g['beta2']:<5} -> {g['selected']:<16} N={g['N']:.1f} "
                  f"k={g['k']:.1f} batch={g['batch']:.2f} attain={g['attainment']:.3f}")
    # policy beta dial
    betas = [round(0.1 * i, 1) for i in range(11)]
    sw = ReplayEvaluator(ParetoSweepObjective(beta_grid=tuple(betas)), W=W, fallback=GRID, runner="inprocess")
    rep = sw.evaluate(code_of(adaptive()), worlds)
    pts = sorted(rep.sweep["points"], key=lambda p: p["beta"])
    rho_beta_N = spearman(betas, [p["N"] for p in pts])
    rho_beta_att = spearman(betas, [p["attainment"] for p in pts])
    print(f"adaptive beta dial: rho(beta, probes) = {rho_beta_N:+.2f}, rho(beta, attainment) = {rho_beta_att:+.2f}; "
          f"pareto reward {rep.sweep['reward']:.3f} (auc {rep.sweep['auc']:.3f}, penalty {rep.sweep['parallel_penalty']:.3f})")
    for p in pts[::2]:
        print(f"   beta={p['beta']:.1f} probes={p['N']:.1f} attainment={p['attainment']:.3f} batch={p['mean_batch']:.2f}")
    # Eq.1 vs Pareto selection among the adaptive variants
    par = ReplayEvaluator(ParetoSweepObjective(beta_grid=(0.2, 0.4, 0.6, 0.8, 1.0)), W=W, fallback=GRID,
                          runner="inprocess")
    adaptive_pool = {n: c for n, c in pool.items() if n.startswith("adaptive_r")}
    pv = {n: par.evaluate(c, worlds).value for n, c in adaptive_pool.items()}
    ev1 = {n: Eq1Objective().score(eps[n]) for n in adaptive_pool}
    pick_p, pick_e = max(pv, key=pv.get), max(ev1, key=ev1.get)
    rho_obj = spearman([pv[n] for n in adaptive_pool], [ev1[n] for n in adaptive_pool])
    print(f"Eq.1 picks {pick_e}, Pareto sweep picks {pick_p}; rank agreement rho={rho_obj:+.2f}")
    plt, png = figure("e7_objective_dials")
    fig, axes = plt.subplots(1, 3, figsize=(12, 3.4))
    for b2 in (0.0, 0.02, 0.1):
        gg = [g for g in grid if g["beta2"] == b2]
        axes[0].plot([g["beta1"] for g in gg], [g["N"] for g in gg], "o-", label=f"beta2={b2}")
    axes[0].set_xlabel("beta1")
    axes[0].set_ylabel("probes N of selected policy")
    axes[0].legend(fontsize=7)
    for b1 in (0.0, 0.01, 0.04):
        gg = [g for g in grid if g["beta1"] == b1]
        axes[1].plot([g["beta2"] for g in gg], [g["batch"] for g in gg], "o-", label=f"beta1={b1}")
    axes[1].set_xlabel("beta2")
    axes[1].set_ylabel("mean batch of selected policy")
    axes[1].legend(fontsize=7)
    axes[2].plot([p["probes_frac"] for p in pts], [p["attainment"] for p in pts], "o-")
    for p in pts[::2]:
        axes[2].annotate(f"{p['beta']:.1f}", (p["probes_frac"], p["attainment"]), fontsize=7)
    axes[2].set_xlabel("probe fraction")
    axes[2].set_ylabel("attainment")
    axes[2].set_title("adaptive policy: beta sweep", fontsize=9)
    fig.tight_layout()
    fig.savefig(png, dpi=130)
    ok1 = rho_b1_N < -0.3
    rows_b2 = []
    for b1 in b1s:
        gg = sorted([g for g in grid if g["beta1"] == b1], key=lambda g: g["beta2"])
        first_switch = next((g["beta2"] for g in gg if g["selected"] != gg[0]["selected"]), None)
        rows_b2.append({"beta1": b1, "batch_at_0": gg[0]["batch"], "batch_at_max": gg[-1]["batch"],
                        "rounds_at_0": gg[0]["k"], "rounds_at_max": gg[-1]["k"], "first_beta2_that_changes_pick":
                        first_switch})
    ok2 = sum(r["batch_at_max"] > r["batch_at_0"] + 1e-9 for r in rows_b2) >= len(rows_b2) / 2
    min_switch = min([r["first_beta2_that_changes_pick"] for r in rows_b2 if r["first_beta2_that_changes_pick"]]
                     or [None], key=lambda x: x if x is not None else 9)
    print("beta2 per beta1 row: " + "; ".join(f"b1={r['beta1']}: batch {r['batch_at_0']:.2f}->{r['batch_at_max']:.2f}, "
                                              f"first switch at b2={r['first_beta2_that_changes_pick']}" for r in rows_b2))
    ok3 = rho_beta_N > 0.5 and rho_beta_att > 0.3
    verdict = (f"beta1 -> fewer probes: {'REPRODUCED' if ok1 else 'NOT reproduced'} (rho {rho_b1_N:+.2f}); "
               f"beta2 -> larger batches / fewer rounds: {'REPRODUCED' if ok2 else 'WEAK'} - the parallelism term only "
               f"changes the argmax from beta2 >= {min_switch} (the doc's 0.005 changes nothing here; rho batch "
               f"{rho_b2_batch:+.2f}, rounds {rho_b2_k:+.2f}); policy beta traces a monotone work/attainment "
               f"frontier: {'REPRODUCED' if ok3 else 'NOT reproduced'} (rho probes {rho_beta_N:+.2f}, attainment "
               f"{rho_beta_att:+.2f})")
    print(verdict)
    save("e7_objective_dials", {"config": {"worlds": a.seeds, "W": W, "grid": GRID, "candidates": len(pool),
                                           "beta1_grid": b1s, "beta2_grid": b2s, "llm": "not used (replay only)"},
                                "candidate_stats": stats, "selection_grid": grid,
                                "beta2_rows": rows_b2,
                                "rho": {"beta1_N": rho_b1_N, "beta2_batch": rho_b2_batch, "beta2_rounds": rho_b2_k,
                                        "policy_beta_probes": rho_beta_N, "policy_beta_attainment": rho_beta_att,
                                        "eq1_vs_pareto_ranking": rho_obj},
                                "beta_sweep": rep.sweep, "eq1_pick": pick_e, "pareto_pick": pick_p,
                                "figure": str(png), "verdict": verdict}, a.out)


if __name__ == "__main__":
    main()
