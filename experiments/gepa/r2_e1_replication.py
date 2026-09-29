"""Retry round 2 - independent replication of E1's matched-ratio comparison (L2) and the
paper's own rollout-ratio metric (Q7), original E1 regime.

Same setup as ``e1_sample_efficiency.py`` (RuleWorld default world, rich feedback, GEPA at
B = 6000, ScalarRL at B = 24000 with lr in {0.5, 2, 8}, the RL arm = the lr with the best
mean *validation* score), on 40 NEW seeds (20-59; E1 used 0-19). Per GEPA run it also
records ``best_discovery_rollouts``: the rollouts spent when the returned candidate was
found (its full D_pareto evaluation included). The paper's "reaches optimal test performance
with 4-35x fewer rollouts" is GRPO's 24000 divided by that number (e.g. IFBench:
24000 / 678 = 35.4). Thresholds: ``docs/methods/gepa/claims-audit.md``, retry round 2.

    python experiments/gepa/r2_e1_replication.py [--seeds 40] [--workers 2]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import RESULTS, paired, parse_args, pool_map, reflection_llm, save, summarize  # noqa: E402

import numpy as np  # noqa: E402

from rsi.domains.ruleworld import make_domain  # noqa: E402
from rsi.gepa import Config, RLConfig, run, run_scalar_rl  # noqa: E402

LRS = [0.5, 2.0, 8.0]
B_GEPA, B_RL = 6000, 24000
SEED0 = int(__import__("os").environ.get("R2_SEED0", 20))


def job(spec):
    arm, seed = spec
    d = make_domain(seed=seed)
    oracle = d.expected(d.oracle_artifact(), "test")
    if arm == "gepa":
        res = run(d, d.seed_artifact(), llm_propose=reflection_llm("sim", d.world),
                  config=Config(max_metric_calls=B_GEPA, seed=seed))
        st = res.state
        return {"arm": arm, "seed": seed, "oracle": oracle, "final_test": d.expected(res.best, "test"),
                "best_val": res.meta["best_val"], "best_discovery_rollouts": int(st.discovery_evals[st.best_idx()]),
                "rollouts": st.counter.total}
    res = run_scalar_rl(d, d.seed_artifact(), config=RLConfig(max_metric_calls=B_RL, lr=float(arm[6:]), seed=seed))
    return {"arm": arm, "seed": seed, "oracle": oracle, "final_test": d.expected(res.best, "test"),
            "best_val": res.meta["best_val"]}


def main():
    a = parse_args("retry-2 E1 replication", default_seeds=40)
    seeds = list(range(SEED0, SEED0 + a.seeds))
    rows = pool_map(job, [(arm, s) for s in seeds for arm in ["gepa"] + [f"rl_lr={lr}" for lr in LRS]], a.workers)
    by = {}
    for r in rows:
        by.setdefault(r["arm"], {})[r["seed"]] = r
    rl_arm = max((f"rl_lr={lr}" for lr in LRS), key=lambda k: np.mean([r["best_val"] for r in by[k].values()]))
    g, rl = by["gepa"], by[rl_arm]
    diff = paired([rl[s]["final_test"] for s in seeds], [g[s]["final_test"] for s in seeds])
    d = np.array([g[s]["final_test"] - rl[s]["final_test"] for s in seeds])
    sd = float(d.std(ddof=1))
    mde = (1.959964 + 0.841621) * sd / np.sqrt(len(d))
    oracle_hit = lambda r: r["final_test"] >= r["oracle"] - 1e-9
    # secondary: pooled with E1's 20 seeds (0-19), same arms and settings
    e1 = json.loads((RESULTS / "e1_sample_efficiency.json").read_text())
    old_rl = e1["selected_rl_arm"]
    og = {r["seed"]: r["final_test"] for r in e1["raw"] if r["arm"] == "gepa"}
    orl = {r["seed"]: r["final_test"] for r in e1["raw"] if r["arm"] == old_rl}
    pool_g = [og[s] for s in sorted(og)] + [g[s]["final_test"] for s in seeds]
    pool_r = [orl[s] for s in sorted(og)] + [rl[s]["final_test"] for s in seeds]
    ratio = [B_RL / max(g[s]["best_discovery_rollouts"], 1) for s in seeds]
    A = {"rl_arm": rl_arm,
         "L2": {"gepa_minus_rl_final": diff, "sd_paired": sd, "mde_80pct_power": float(mde),
                "gepa_wins": int(np.sum(d > 0)), "rl_wins": int(np.sum(d < 0)),
                "rl_reached_oracle": int(sum(oracle_hit(rl[s]) for s in seeds)),
                "gepa_reached_oracle": int(sum(oracle_hit(g[s]) for s in seeds)),
                "gepa_final": summarize([g[s]["final_test"] for s in seeds]),
                "rl_final": summarize([rl[s]["final_test"] for s in seeds]),
                "pass": bool(diff["lo"] > 0), "rl_significantly_ahead": bool(diff["hi"] < 0),
                "pooled_60_seeds_secondary": paired(pool_r, pool_g)},
         "Q7": {"ratio_24000_over_rollouts_to_best": summarize(ratio), "median_ratio": float(np.median(ratio)),
                "min_ratio": float(np.min(ratio)), "max_ratio": float(np.max(ratio)),
                "frac_in_4_35": float(np.mean([4 <= x <= 35 for x in ratio])),
                "median_rollouts_to_best": float(np.median([g[s]["best_discovery_rollouts"] for s in seeds])),
                "pass_median_in_4_35": bool(4 <= np.median(ratio) <= 35)}}
    v = (f"L2 (40 new seeds): GEPA@6000 - RL@24000 ({rl_arm}) = {diff['mean_diff']:+.3f} [{diff['lo']:+.3f}, "
         f"{diff['hi']:+.3f}] (MDE at 80% power {mde:.3f}); GEPA wins {A['L2']['gepa_wins']}, RL wins "
         f"{A['L2']['rl_wins']}; oracle reached RL {A['L2']['rl_reached_oracle']}/40, GEPA "
         f"{A['L2']['gepa_reached_oracle']}/40. Q7: 24000 / rollouts-to-best median {np.median(ratio):.1f}x "
         f"(range {np.min(ratio):.1f}-{np.max(ratio):.1f}x, {A['Q7']['frac_in_4_35']:.0%} of seeds in 4-35x).")
    save("r2_e1_replication", {"experiment": "retry-2 E1 replication", "config": {
        "seeds": [SEED0, SEED0 + a.seeds - 1], "B_gepa": B_GEPA, "B_rl": B_RL, "lrs": LRS},
        "analysis": A, "verdict": v, "raw": rows}, a.out)
    print(v)


if __name__ == "__main__":
    main()
