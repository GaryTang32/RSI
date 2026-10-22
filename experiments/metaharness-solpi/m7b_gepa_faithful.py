"""M7b - M7 with a faithful GEPA arm (``gepa_minibatch``), retry round 2 follow-up (preregistration P7).

    python experiments/metaharness-solpi/m7b_gepa_faithful.py --seeds 20 --workers 2 [--reanalyse]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import RESULTS, parse_args, pool_map, save, summarize  # noqa: E402
from m7_optimizers_equal_budget import BUDGET, K, N_ITER, job as m7_job, match_next_best, summary_vs_mh  # noqa: E402

M7_ARMS = ("metaharness", "best_of_n", "openevolve", "ttt_discover", "gepa")
NEW = "gepa_minibatch"
FAITHFUL = ["best_of_n", "openevolve", "ttt_discover", NEW]
# MH and TTT-D have identical final-best means whose float difference (1.7e-17) must not count as "> 0" (P1)
TOL = 1e-9


def job(spec: tuple[str, int]) -> dict:
    return m7_job(spec, "m7b")


def outcome(reproduced: bool, partial: bool) -> str:
    return ("reproduced" if reproduced else "partially reproduced" if partial else "not reproduced") \
        + " on the CPU analogue"


def main():
    args = parse_args(__doc__.splitlines()[0], default_seeds=20,
                      extra=lambda ap: ap.add_argument("--reanalyse", action="store_true"))
    seeds = list(range(args.seeds))
    m7 = json.loads((RESULTS / "m7_optimizers_equal_budget.json").read_text())
    if args.reanalyse:
        rows = json.loads((RESULTS / "m7b_gepa_faithful.json").read_text())["per_seed"]
    else:  # the new arm, plus MH on seeds 0-1 as a determinism check against M7
        rows = pool_map(job, [(NEW, s) for s in seeds] + [("metaharness", s) for s in (0, 1)], min(args.workers, 2))
    new = [r for r in rows if r["arm"] == NEW]
    check = [r for r in rows if r["arm"] == "metaharness"]
    by = {a: {r["seed"]: r for r in m7["per_seed"] if r["arm"] == a} for a in M7_ARMS}
    determinism = {r["seed"]: r["curve"] == by["metaharness"][r["seed"]]["curve"] for r in check}
    by[NEW] = {r["seed"]: r for r in new}
    seeds = sorted(set.intersection(*[set(v) for v in by.values()]))
    arms = list(M7_ARMS) + [NEW]
    summ, cmp = summary_vs_mh(by, seeds, arms)
    all_ci = all(cmp[a][k].get("lo", -1) > TOL for a in FAITHFUL for k in ("final_best", "median_candidate"))
    means = all(cmp[a]["final_best"].get("mean_diff", 0) > TOL for a in FAITHFUL)
    q4_analogue = outcome(all_ci, means)
    q5r = {"next_best_any_faithful": match_next_best(by, seeds, FAITHFUL, TOL),
           "next_best_openevolve_ttt": match_next_best(by, seeds, ["openevolve", "ttt_discover"], TOL)}
    per_arm_lead = {a: summarize([by["metaharness"][s]["final_best"] - by[a][s]["final_best"] for s in seeds])
                    for a in FAITHFUL}
    p = q5r["next_best_any_faithful"]
    q5_analogue = outcome(p["speed_ok"] and p["lead_ok"], p["speed_ok"] or p["lead_ok"])
    dedup = {a: int(sum(by[a][s].get("dedup_retries", 0) for s in seeds)) for a in arms}
    for a in arms:
        print(a, "final", summ[a]["final_best"], "median", summ[a]["median_candidate"], "dedup", dedup[a])
    for a in cmp:
        print("MH -", a, "final", cmp[a]["final_best"], "median", cmp[a]["median_candidate"])
    print("per-arm lead", per_arm_lead)
    print("Q5", {k: {kk: vv for kk, vv in v.items() if kk != "evals_to_match_per_seed"} for k, v in q5r.items()})
    print("determinism", determinism, "| Q4 analogue:", q4_analogue, "| Q5 analogue:", q5_analogue)
    save("m7b_gepa_faithful", {
        "claim": "Q4/Q5 with a faithful GEPA arm [MH §4.1, Table 4, Figs. 1, 4]",
        "preregistration": "claims-audit-metaharness.md, Retry round 2: preregistration, P7",
        "config": {"seeds": seeds, "iterations": N_ITER, "k": K, "budget": BUDGET, "proposer": "MockProposer",
                   "new_arm": NEW, "other_arms_from": "m7_optimizers_equal_budget.json"},
        "determinism_check_metaharness_curves_identical": determinism,
        "summary": summ, "paired_mh_minus_arm": cmp, "per_arm_lead_final_best": per_arm_lead, "q5": q5r,
        "dedup_retries_total": dedup, "analogue_outcome_q4": q4_analogue, "analogue_outcome_q5": q5_analogue,
        "verdict_rule": "P7: Q4 and Q5 are NOT TESTABLE HERE (c) whatever this shows; the mock is Markovian (N4) "
                        "and cannot express non-Markovian full-history use",
        "per_seed": new + check})


if __name__ == "__main__":
    main()
