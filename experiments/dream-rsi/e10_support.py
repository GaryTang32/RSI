"""E10: replay can only score choices that were tried; a "go deeper" policy on shallow vs deep recorded worlds.

    python experiments/dream-rsi/e10_support.py [--llm sim|claude:haiku] [--seeds K] [--quick]
"""
import numpy as np
from _common import (domain_of, figure, fmt, live_search, llm_policies, online_eq1, parse_args, pmap, runner_for, save,
                     save_figure, summ)

from rsi.dream import Eq1Objective, ReplayEvaluator, adaptive, code_of, parallel_refine, rules

W = 4
OBJ = dict(beta1=0.002, beta2=0.005)
RECORDS = {"shallow_6x2": (6, 2), "deep_6x9": (6, 9)}


def policies():
    return {"go_deeper_3x9": code_of(rules(plan_w=3, plan_r=9, top_k=2)),
            "shallow_6x2": code_of(rules(plan_w=6, plan_r=2)),
            "adaptive": code_of(adaptive()),
            "adaptive_deep": code_of(adaptive(default_beta=1.0, patience_lo=3.0, patience_hi=6.0))}


def run1(code, seed, grid, runner="inprocess"):
    """A recording, or the true online value of the policy's own plan: no per-round call cap."""
    return live_search(domain_of("synthetic", seed), W, grid, seed, code, sandbox=runner, hard_max_branch=12,
                       hard_max_refine=12, round_budget=None)


def online(job):
    name, code, seed = job
    r = run1(code, seed, (6, 4), runner_for(name)).trajectory[0]
    return name, online_eq1(r, OBJ["beta1"], OBJ["beta2"]), r["N"]


def main():
    a = parse_args("E10 support limitation", default_seeds=8)
    n_true = 15 if a.quick else 50
    pol = policies()
    rec_code = code_of(parallel_refine())
    worlds = {k: [run1(rec_code, 700 + i, g).meta["worlds"][0] for i in range(a.seeds)] for k, g in RECORDS.items()}
    pol.update(llm_policies(a.llm, worlds["shallow_6x2"], n=1, W=W, fallback=(6, 4), hard_max=(12, 12),
                            objective=Eq1Objective(normalize=False, **OBJ)))
    true: dict[str, list] = {}
    probes: dict[str, list] = {}
    for n, v, N in pmap(online, [(n, c, 5000 + s) for n, c in pol.items() for s in range(n_true)], a.workers):
        true.setdefault(n, []).append(v)
        probes.setdefault(n, []).append(N)
    same: dict[str, list] = {}  # true online value on the SAME world seeds as the records (common random numbers)
    for n, v, N in pmap(online, [(n, c, 700 + i) for n, c in pol.items() for i in range(a.seeds)], a.workers):
        same.setdefault(n, []).append(v)
    res = {}
    for n, code in pol.items():
        row = {"true_population": summ(true[n]), "true": summ(same[n]), "true_probes": float(np.mean(probes[n]))}
        for rk, ws in worlds.items():
            for rule in ("clip", "no_reward"):
                ev = ReplayEvaluator(Eq1Objective(normalize=False, support=rule, **OBJ), W=W, fallback=(6, 4),
                                     runner=runner_for(n), hard_max=(12, 12))
                rep = ev.evaluate(code, ws)
                row[f"replay_{rk}_{rule}"] = rep.value
                row[f"out_of_support_{rk}"] = bool(any(e.out_of_support for e in rep.episodes))
            row[f"error_{rk}_clip"] = row[f"replay_{rk}_clip"] - row["true"]["mean"]
        res[n] = row
        print(f"{n:<15} true (same worlds) {row['true']['mean']:+.4f}, population {fmt(row['true_population'])} | "
              f"replay shallow {row['replay_shallow_6x2_clip']:+.4f} "
              f"(err {row['error_shallow_6x2_clip']:+.4f}, out-of-support {row['out_of_support_shallow_6x2']}) | "
              f"replay deep {row['replay_deep_6x9_clip']:+.4f} (err {row['error_deep_6x9_clip']:+.4f})")
    rank = {k: sorted(pol, key=lambda n: -res[n][f"replay_{k}_clip"]) for k in RECORDS}
    rank_true = sorted(pol, key=lambda n: -res[n]["true_population"]["mean"])
    print(f"true ranking {rank_true}; replay on shallow record {rank['shallow_6x2']}; "
          f"on deep record {rank['deep_6x9']}")
    plt, png = figure("e10_support")
    fig, ax = plt.subplots(figsize=(6.5, 3.6))
    xs = np.arange(len(pol))
    ax.bar(xs - 0.27, [res[n]["true"]["mean"] for n in pol], 0.27, label="true online (same worlds)", color="#55A868")
    ax.bar(xs, [res[n]["replay_shallow_6x2_clip"] for n in pol], 0.27, label="replay, shallow record", color="#C44E52")
    ax.bar(xs + 0.27, [res[n]["replay_deep_6x9_clip"] for n in pol], 0.27, label="replay, deep record", color="#4C72B0")
    ax.set_xticks(xs)
    ax.set_xticklabels(list(pol), fontsize=7)
    ax.set_ylabel("Eq.1 value (raw, beta1=0.002)")
    ax.legend(fontsize=7)
    save_figure(fig, png)
    e_sh, e_dp = res["go_deeper_3x9"]["error_shallow_6x2_clip"], res["go_deeper_3x9"]["error_deep_6x9_clip"]
    recovers = abs(e_dp) < max(0.005, abs(e_sh) / 4)
    verdict = (f"{'REPRODUCED' if e_sh < -0.01 and recovers else 'PARTIAL'}: the go-deeper policy is "
               f"{'underestimated' if e_sh < -0.01 else 'overestimated' if e_sh > 0.01 else 'about right'} on the "
               f"shallow record (error {e_sh:+.3f}; its plan is out of support) and the estimate "
               f"{'recovers' if recovers else 'does not recover'} when the record covers its depth "
               f"(error {e_dp:+.3f}); replay ranking on the shallow record {rank['shallow_6x2']} vs deep record "
               f"{rank['deep_6x9']} vs true (population) {rank_true}. "
               "Errors are against the true online value on the same world seeds (common random numbers), so they "
               "isolate the support effect from world-sampling noise")
    print(verdict)
    save("e10_support", {"config": {"records": RECORDS, "recorded_worlds": a.seeds, "true_value_worlds": n_true,
                                    "objective": OBJ | {"normalize": False}, "W": W,
                                    "llm": a.llm if a.llm != "sim" else "not used",
                                    "llm_policies": {n: c for n, c in pol.items() if n.startswith("llm_")}},
                         "results": res, "ranking": {"true": rank_true, **rank}, "figure": str(png),
                         "verdict": verdict}, a.out)


if __name__ == "__main__":
    main()
