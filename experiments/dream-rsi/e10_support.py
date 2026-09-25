"""E10 - Replay can only test choices that were actually tried.

Critique [doc "Replay can only test choices that were actually tried"; paper App.B.2 "a
requested plan beyond the frozen trace's trace_branch_count or trace_refine_count is out
of support and cannot earn replay reward"]: a policy whose value comes from depth the
record does not contain cannot be scored fairly.

Synthetic worlds (which contain late-blooming branches) are recorded with a shallow grid
(6 x depth 2) and a deep grid (6 x depth 9). A "go deeper" policy (3 roots, then keep
refining the best 2 branches to depth 9), the shallow refine-all policy and the adaptive
policy are scored (a) by replay on each record, under the two support rules (clip / no
reward), and (b) by their true online value on fresh worlds (own plan, no clipping).
Objective: Eq.1 on raw scores with beta1 = 0.002 (depth is worth paying for here).

    python experiments/dream-rsi/e10_support.py [--seeds K] [--quick]
"""
import numpy as np
from _common import figure, fmt, parse_args, pmap, save, summ

from rsi.dream import Config, DreamRSILoop, Eq1Objective, ReplayEvaluator, adaptive, code_of, rules
from rsi.domains.discovery import SyntheticConfig, SyntheticDomain

W = 4
OBJ = dict(beta1=0.002, beta2=0.005)
RECORDS = {"shallow_6x2": (6, 2), "deep_6x9": (6, 9)}


def policies():
    return {"go_deeper_3x9": code_of(rules(plan_w=3, plan_r=9, top_k=2)),
            "shallow_6x2": code_of(rules(plan_w=6, plan_r=2)),
            "adaptive": code_of(adaptive()),
            "adaptive_deep": code_of(adaptive(default_beta=1.0, patience_lo=3.0, patience_hi=6.0))}


def run1(code, seed, grid):
    dom = SyntheticDomain(SyntheticConfig(seed=seed))
    cfg = Config(rounds=1, W=W, branch_count=grid[0], refine_count=grid[1], dream=False, sandbox="inprocess",
                 seed=seed, hard_max_branch=12, hard_max_refine=12, agent_workers=1)
    return DreamRSILoop(dom.as_task(), dom.mock_agent(), config=cfg, initial_policy=code).run()


def online(job):
    name, code, seed = job
    r = run1(code, seed, (6, 4)).trajectory[0]
    return name, r["round_best"] - OBJ["beta1"] * r["N"] + OBJ["beta2"] * r["N"] / max(1, r["k"]), r["N"]


def main():
    a = parse_args("E10 support limitation", default_seeds=8)
    n_true = 15 if a.quick else 50
    pol = policies()
    from rsi.dream import parallel_refine
    rec_code = code_of(parallel_refine())
    worlds = {k: [run1(rec_code, 700 + i, g).meta["worlds"][0] for i in range(a.seeds)] for k, g in RECORDS.items()}
    true: dict[str, list] = {}
    probes: dict[str, list] = {}
    for n, v, N in pmap(online, [(n, c, 5000 + s) for n, c in pol.items() for s in range(n_true)], a.workers):
        true.setdefault(n, []).append(v)
        probes.setdefault(n, []).append(N)
    same: dict[str, list] = {}      # true online value on the SAME world seeds as the records (paired, CRN)
    for n, v, N in pmap(online, [(n, c, 700 + i) for n, c in pol.items() for i in range(a.seeds)], a.workers):
        same.setdefault(n, []).append(v)
    res = {}
    for n, code in pol.items():
        row = {"true_population": summ(true[n]), "true": summ(same[n]), "true_probes": float(np.mean(probes[n]))}
        for rk, ws in worlds.items():
            for rule in ("clip", "no_reward"):
                ev = ReplayEvaluator(Eq1Objective(normalize=False, support=rule, **OBJ), W=W, fallback=(6, 4),
                                     runner="inprocess", hard_max=(12, 12))
                rep = ev.evaluate(code, ws)
                row[f"replay_{rk}_{rule}"] = rep.value
                row[f"out_of_support_{rk}"] = bool(any(e.out_of_support for e in rep.episodes))
            row[f"error_{rk}_clip"] = row[f"replay_{rk}_clip"] - row["true"]["mean"]
        res[n] = row
        print(f"{n:<15} true (same worlds) {row['true']['mean']:+.4f}, population {fmt(row['true_population'])} | replay shallow {row['replay_shallow_6x2_clip']:+.4f} "
              f"(err {row['error_shallow_6x2_clip']:+.4f}, out-of-support {row['out_of_support_shallow_6x2']}) | "
              f"replay deep {row['replay_deep_6x9_clip']:+.4f} (err {row['error_deep_6x9_clip']:+.4f})")
    rank = {k: sorted(pol, key=lambda n: -res[n][f"replay_{k}_clip"]) for k in RECORDS}
    rank_true = sorted(pol, key=lambda n: -res[n]["true_population"]["mean"])
    print(f"true ranking {rank_true}; replay on shallow record {rank['shallow_6x2']}; on deep record {rank['deep_6x9']}")
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
    fig.tight_layout()
    fig.savefig(png, dpi=130)
    d = res["go_deeper_3x9"]
    under = d["error_shallow_6x2_clip"] < -0.01
    recovers = abs(d["error_deep_6x9_clip"]) < max(0.005, abs(d["error_shallow_6x2_clip"]) / 4)
    verdict = (f"{'REPRODUCED' if under and recovers else 'PARTIAL'}: the go-deeper policy is underestimated on the "
               f"shallow record (error {d['error_shallow_6x2_clip']:+.3f}; its plan is out of support) and the estimate "
               f"recovers when the record covers its depth (error {d['error_deep_6x9_clip']:+.3f}); replay ranking on the "
               f"shallow record {rank['shallow_6x2']} vs deep record {rank['deep_6x9']} vs true (population) {rank_true}. "
               "Errors are against the true online value on the same world seeds (common random numbers), so they "
               "isolate the support effect from world-sampling noise")
    print(verdict)
    save("e10_support", {"config": {"records": RECORDS, "recorded_worlds": a.seeds, "true_value_worlds": n_true,
                                    "objective": OBJ | {"normalize": False}, "W": W, "llm": "not used"},
                         "results": res, "ranking": {"true": rank_true, **rank}, "figure": str(png),
                         "verdict": verdict}, a.out)


if __name__ == "__main__":
    main()
