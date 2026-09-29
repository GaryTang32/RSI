"""E2c (retry round 2, preregistered P4 in docs/methods/rrsi/claims-audit.md section 6).

The Table 2 ablation, rerun at Table 2's own setting: the workspace instance's released
hyperparameters (T = 20, k = 2, m = 2, b_min = 1, b_max = 3, beta0 = 0.10, beta1 = 35.4, w_s = 1414,
w_c = 15, w_n = 0.5, n_prune = 4, n_fail_traces = 60, n_success_traces = 6) with delta calibrated per
run (the preset's fixed 0.004 is Harvey LAB's band, not HarnessWorld's). 100 worlds, same seeds for
every arm. Claims: L4 (-proposal: evolve about the same, OOD lower), L3 (removing any group raises
evolve and lowers OOD), L1 (unregularized: highest evolve, OOD ~ H_0), C7 (budget-only vs
selector-only; a third-party hypothesis) and M35 (robustness to the unregularized definition).
Sensitivity S1: full and -proposal with a prompt-collapsing mock proposer (E9's mild bias).
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import fmt, paired, pmap, run_hw, save, strip_curves, summarize  # noqa: E402

from rsi.rrsi import RegularizerSwitches as RS  # noqa: E402
from rsi.rrsi.config import PRESETS  # noqa: E402

WORKSPACE = {**PRESETS["workspace"], "delta": None}
COLLAPSE = {"component_bias": {"prompt": 8.0, "control_flow": 2.0}}
ARMS = {
    "full": (RS.full(), None),
    "-proposal": (RS.no_proposal(), None),
    "-proposal (prune as proposal)": (RS.no_proposal(prune_in_proposal_group=True), None),
    "-acceptance": (RS.no_acceptance(), None),
    "unregularized": (RS.none(), None),
    "budget-only": (RS.budget_only(), None),
    "unregularized-argmax": (RS.none().but(selection="argmax", name="unreg_argmax"), None),
    "unregularized-b8": (RS.none().but(constant_budget=8, name="unreg_b8"), None),
    "S1 full": (RS.full(), COLLAPSE),
    "S1 -proposal": (RS.no_proposal(), COLLAPSE),
}
MAIN4 = ("unregularized", "-proposal", "-acceptance", "full")


def main():
    ap = argparse.ArgumentParser(description="E2c: ablations at the workspace preset")
    ap.add_argument("--seeds", type=int, default=100)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--T", type=int, default=None)
    a = ap.parse_args()
    cfg = dict(WORKSPACE)
    if a.T:
        cfg["T"] = a.T
    t0 = time.time()
    jobs = [{"seed": s, "arm": sw, "label": lab, "cfg": cfg, **({"proposer": prop} if prop else {})}
            for s in range(a.seeds) for lab, (sw, prop) in ARMS.items()]
    rows = pmap(run_hw, jobs, a.workers)
    metrics = ("measured_gain", "evolve_gain", "holdout_gain", "ood_gain", "unseen_gain", "token_ratio",
               "n_accepted", "hitchhiker_rate", "leaks_in_final", "n_mechanisms", "delta")
    summ = summarize(rows, metrics=metrics)
    pm = ("measured_gain", "evolve_gain", "holdout_gain", "ood_gain", "token_ratio")
    vs_full = {lab: {m: paired(rows, "full", lab, m) for m in pm} for lab in ARMS
               if lab not in ("full", "S1 full", "S1 -proposal")}
    s1 = {m: paired(rows, "S1 full", "S1 -proposal", m) for m in pm}
    # --- preregistered tests ----------------------------------------------------------------------
    def l4_ok(pd):
        return pd["ood_gain"]["hi"] < 0, pd["measured_gain"]["lo"] > -0.01
    ood_p, evo_p = l4_ok(vs_full["-proposal"])
    ood_s, evo_s = l4_ok(s1)
    if ood_p and evo_p:
        l4 = "REPRODUCED"
    elif ood_p:
        l4 = "PARTIAL (OOD lower, evolve lower beyond the 1-pt margin)"
    elif ood_s and evo_s:
        l4 = "PARTIAL (only with a prompt-collapsing proposer, S1)"
    else:
        l4 = "NOT REPRODUCED"
    l3_arms = {lab: (vs_full[lab]["measured_gain"]["lo"] > 0 and vs_full[lab]["ood_gain"]["hi"] < 0)
               for lab in ("unregularized", "-acceptance", "-proposal")}
    n3 = sum(l3_arms.values())
    l3 = "REPRODUCED" if n3 == 3 else ("PARTIAL" if n3 else "NOT REPRODUCED")
    ev_means = {lab: summ[lab]["measured_gain"]["mean"] for lab in MAIN4}
    unreg_highest = max(ev_means, key=ev_means.get) == "unregularized" and vs_full["unregularized"]["measured_gain"]["lo"] > 0
    uo = summ["unregularized"]["ood_gain"]
    if unreg_highest and uo["lo"] >= -0.03 and uo["hi"] <= 0.03:
        l1 = "REPRODUCED"
    elif uo["mean"] >= summ["full"]["ood_gain"]["mean"]:
        l1 = "NOT REPRODUCED"
    elif unreg_highest and abs(uo["mean"]) <= 0.03:
        l1 = "PARTIAL (OOD point estimate within +-3 pts, CI not)"
    else:
        l1 = "PARTIAL"
    sel_minus_budget = {m: paired(rows, "budget-only", "-proposal", m) for m in ("ood_gain", "unseen_gain")}
    c7 = ("CONTRADICTED" if (summ["budget-only"]["ood_gain"]["hi"] < 0.5 * summ["full"]["ood_gain"]["mean"]
                             and sel_minus_budget["ood_gain"]["lo"] > 0) else "not contradicted")
    m35 = {lab: (vs_full[lab]["measured_gain"]["lo"] > 0 and vs_full[lab]["ood_gain"]["hi"] < 0)
           for lab in ("unregularized", "unregularized-argmax", "unregularized-b8")}
    tests = {"L4": {"verdict": l4, "primary_ood_lower": ood_p, "primary_evolve_noninferior": evo_p,
                    "S1_ood_lower": ood_s, "S1_evolve_noninferior": evo_s},
             "L3": {"verdict": l3, "per_arm": l3_arms},
             "L1": {"verdict": l1, "unregularized_highest_evolve": unreg_highest,
                    "unregularized_ood_gain": uo},
             "C7": {"verdict": c7, "budget_only_ood": summ["budget-only"]["ood_gain"],
                    "half_full_ood": 0.5 * summ["full"]["ood_gain"]["mean"],
                    "selector_minus_budget": sel_minus_budget},
             "M35_robustness": {"all_variants_overfit": all(m35.values()), "per_variant": m35}}
    table = [{"arm": lab, "measured_evolve": fmt(summ[lab]["measured_gain"]), "true_evolve": fmt(summ[lab]["evolve_gain"]),
              "holdout": fmt(summ[lab]["holdout_gain"]), "ood": fmt(summ[lab]["ood_gain"]),
              "tokens_x_H0": f"{summ[lab]['token_ratio']['mean']:.2f} [{summ[lab]['token_ratio']['lo']:.2f}, "
                             f"{summ[lab]['token_ratio']['hi']:.2f}]",
              "leaks_in_final": round(summ[lab]["leaks_in_final"]["mean"], 2),
              "n_mechanisms": round(summ[lab]["n_mechanisms"]["mean"], 1)} for lab in ARMS]
    out = {"experiment": "E2c ablations at the workspace preset (retry round 2: P4)",
           "config": {"seeds": a.seeds, "cfg": cfg, "arms": {k: v[0].to_json() for k, v in ARMS.items()},
                      "S1_proposer": COLLAPSE},
           "table": table, "summary": summ, "paired_vs_full": vs_full, "S1_minus_proposal_vs_full": s1,
           "tests": tests, "wall_s": round(time.time() - t0, 1), "rows": strip_curves(rows)}
    save("e2c_ablations_workspace", out)
    for r in table:
        print(r)
    for lab, v in vs_full.items():
        print("vs full:", lab, {m: (round(x["mean_diff"] * (1 if m == "token_ratio" else 100), 2),
                                    round(x["lo"] * (1 if m == "token_ratio" else 100), 2),
                                    round(x["hi"] * (1 if m == "token_ratio" else 100), 2)) for m, x in v.items()})
    print("S1 -proposal - full:", {m: (round(x["mean_diff"] * 100, 2), round(x["lo"] * 100, 2),
                                      round(x["hi"] * 100, 2)) for m, x in s1.items()})
    for k, v in tests.items():
        print(k, v)


if __name__ == "__main__":
    main()
