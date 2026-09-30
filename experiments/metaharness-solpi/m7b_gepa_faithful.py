"""M7b - M7 with a faithful GEPA arm (``gepa_minibatch``), retry round 2 follow-up (preregistration P7).

A reviewer showed that M7's ``gepa`` arm was not GEPA: it handed the proposer the parent's FULL traces and took the
Pareto front over 3 dataset-level units. ``gepa_minibatch`` (``rsi.metaharness.baselines``) uses GEPA's
per-instance front with dominated-program removal and a 3-example reflective minibatch. This script runs that arm
at M7's exact configuration, re-runs the ``metaharness`` arm for seeds 0-1 as a determinism check, and re-analyses
Q4/Q5 with the other arms taken unchanged from ``m7_optimizers_equal_budget.json``.

    python experiments/metaharness-solpi/m7b_gepa_faithful.py --seeds 20 --workers 2
    python experiments/metaharness-solpi/m7b_gepa_faithful.py --reanalyse   # from the saved per-seed rows, $0

Mean differences within ``TOL`` of 0 count as 0 (a tie): MH and TTT-D have identical final-best means, and their
float difference is 1.7e-17, which must not count as "> 0" under P1's rule.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import RESULTS, fresh_dir, paired, parse_args, pool_map, save, summarize  # noqa: E402
from mh_common import drop_traces  # noqa: E402

import numpy as np  # noqa: E402

from rsi.domains.memoclassify import make_domain  # noqa: E402
from rsi.metaharness import Config, MemoClassifyLibrary, MockProposer, StructuredOptimizerProposer, run  # noqa: E402

N_ITER, K = 20, 2
BUDGET = N_ITER * K
M7_ARMS = ("metaharness", "best_of_n", "openevolve", "ttt_discover", "gepa")
NEW = "gepa_minibatch"
TOL = 1e-9


def job(spec):
    arm, seed = spec
    dom = make_domain(seed=seed)
    prop = MockProposer(MemoClassifyLibrary(), seed=seed)
    if arm != "metaharness":
        prop = StructuredOptimizerProposer(prop, arm, seed=seed)
    res = run(dom, dom.seed_artifact("fewshot_all"), llm_task=dom.make_model("A"), proposer=prop,
              config=Config(iterations=N_ITER, k=K, history_mode="full", eval_budget=BUDGET, seed=seed,
                            shadow_monitor=False),
              out_dir=fresh_dir("m7b", f"{arm}_s{seed}"), baselines=dom.baselines())
    store = res.loop.store
    base_best = max(store.scores(n)["score"] for n in dom.baselines())
    curve = [base_best] + [c["best_so_far"] for c in res.meta["curve"]]
    curve += [curve[-1]] * (BUDGET + 1 - len(curve))
    cands = [store.scores(n)["score"] for n in store.names() if n not in dom.baselines() and store.scores(n)]
    best = res.meta["best_system"]
    test = res.meta["final"]["splits"]["test"]["results"]
    dedup = sum(int((json.loads(p.read_text()).get("proposer_meta") or {}).get("dedup_retries", 0) or 0)
                for p in store.sessions_dir().glob("iter*/meta.json"))
    drop_traces(store.root)
    return {"arm": arm, "seed": seed, "curve": curve, "final_best": curve[-1],
            "median_candidate": float(np.median(cands)) if cands else None, "n_evaluated": res.meta["n_evaluated"],
            "selected": best, "selected_test": test.get(best, {}).get("score"),
            "fewshot_all_test": test["fewshot_all"]["score"], "dedup_retries": dedup}


def evals_to_reach(curve, target):
    for i, v in enumerate(curve):
        if v >= target - 1e-12:
            return i
    return None


def q5(by, seeds, pool):
    e, lead, which = [], [], []
    for s in seeds:
        nb = max(pool, key=lambda a: by[a][s]["final_best"])
        tgt = by[nb][s]["final_best"]
        x = evals_to_reach(by["metaharness"][s]["curve"], tgt)
        e.append(BUDGET + 1 if x is None else x)
        lead.append(by["metaharness"][s]["final_best"] - tgt)
        which.append(nb)
    return {"evals_to_match_per_seed": e, "median_evals_to_match": float(np.median(e)),
            "n_never": sum(x == BUDGET + 1 for x in e), "lead": summarize(lead), "next_best_arm_per_seed": which,
            "speed_ok": float(np.median(e)) <= BUDGET / 10, "lead_ok": float(np.mean(lead)) >= 0.10 - TOL}


def main():
    args = parse_args(__doc__.splitlines()[0], default_seeds=20,
                      extra=lambda ap: ap.add_argument("--reanalyse", action="store_true"))
    seeds = list(range(args.seeds))
    m7 = json.loads((RESULTS / "m7_optimizers_equal_budget.json").read_text())
    if args.reanalyse:
        rows = json.loads((RESULTS / "m7b_gepa_faithful.json").read_text())["per_seed"]
    else:
        jobs = [(NEW, s) for s in seeds] + [("metaharness", s) for s in (0, 1)]
        rows = pool_map(job, jobs, min(args.workers, 2))
    new = [r for r in rows if r["arm"] == NEW]
    check = [r for r in rows if r["arm"] == "metaharness"]
    by = {a: {r["seed"]: r for r in m7["per_seed"] if r["arm"] == a} for a in M7_ARMS}
    determinism = {r["seed"]: r["curve"] == by["metaharness"][r["seed"]]["curve"] for r in check}
    by[NEW] = {r["seed"]: r for r in new}
    seeds = sorted(set.intersection(*[set(v) for v in by.values()]))
    arms = list(M7_ARMS) + [NEW]
    summ = {a: {k: summarize([by[a][s][k] for s in seeds]) for k in ("final_best", "median_candidate",
                                                                         "selected_test")} for a in arms}
    cmp = {a: {k: paired([by[a][s][k] for s in seeds], [by["metaharness"][s][k] for s in seeds])
               for k in ("final_best", "median_candidate", "selected_test")} for a in arms if a != "metaharness"}
    faithful = ["best_of_n", "openevolve", "ttt_discover", NEW]
    all_ci = all(cmp[a][k].get("lo", -1) > TOL for a in faithful for k in ("final_best", "median_candidate"))
    means = all(cmp[a]["final_best"].get("mean_diff", 0) > TOL for a in faithful)
    q4_analogue = ("reproduced on the CPU analogue" if all_ci else
                   "partially reproduced on the CPU analogue" if means else "not reproduced on the CPU analogue")
    q5r = {"next_best_any_faithful": q5(by, seeds, faithful),
           "next_best_openevolve_ttt": q5(by, seeds, ["openevolve", "ttt_discover"])}
    per_arm_lead = {a: summarize([by["metaharness"][s]["final_best"] - by[a][s]["final_best"] for s in seeds])
                    for a in faithful}
    p = q5r["next_best_any_faithful"]
    q5_analogue = ("reproduced on the CPU analogue" if p["speed_ok"] and p["lead_ok"] else
                   "partially reproduced on the CPU analogue" if p["speed_ok"] or p["lead_ok"]
                   else "not reproduced on the CPU analogue")
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
