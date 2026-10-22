"""E2c (preregistered P4): the Table 2 ablation at the workspace preset; claims L4, L3, L1, C7, M35 and sensitivity S1.

Run: python experiments/rrsi/e2c_ablations_workspace.py [--seeds 100] [--workers 2] [--T T]
"""
import time

from _common import ci_pts, fmt, fmt_ratio, pairs, pmap, retry_args, run_hw, save, strip_curves, summarize

from rsi.rrsi import RegularizerSwitches as RS
from rsi.rrsi.config import PRESETS

WORKSPACE = {**PRESETS["workspace"], "delta": None}   # delta calibrated per run: the preset's 0.004 is Harvey LAB's
COLLAPSE = {"component_bias": {"prompt": 8.0, "control_flow": 2.0}}   # S1: E9's mild prompt-collapsing proposer
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
PM = ("measured_gain", "evolve_gain", "holdout_gain", "ood_gain", "token_ratio")


def overfits(pd: dict) -> bool:
    return pd["measured_gain"]["lo"] > 0 and pd["ood_gain"]["hi"] < 0


def l4_parts(pd: dict) -> tuple[bool, bool]:
    """(OOD lower, evolve non-inferior at the 1-pt margin)."""
    return pd["ood_gain"]["hi"] < 0, pd["measured_gain"]["lo"] > -0.01


def preregistered_tests(summ: dict, vs_full: dict, s1: dict, sel_minus_budget: dict) -> dict:
    (ood_p, evo_p), (ood_s, evo_s) = l4_parts(vs_full["-proposal"]), l4_parts(s1)
    if ood_p and evo_p:
        l4 = "REPRODUCED"
    elif ood_p:
        l4 = "PARTIAL (OOD lower, evolve lower beyond the 1-pt margin)"
    elif ood_s and evo_s:
        l4 = "PARTIAL (only with a prompt-collapsing proposer, S1)"
    else:
        l4 = "NOT REPRODUCED"
    l3_arms = {lab: overfits(vs_full[lab]) for lab in ("unregularized", "-acceptance", "-proposal")}
    n3 = sum(l3_arms.values())
    l3 = "REPRODUCED" if n3 == 3 else ("PARTIAL" if n3 else "NOT REPRODUCED")
    ev_means = {lab: summ[lab]["measured_gain"]["mean"] for lab in MAIN4}
    unreg_highest = (max(ev_means, key=ev_means.get) == "unregularized"
                     and vs_full["unregularized"]["measured_gain"]["lo"] > 0)
    uo = summ["unregularized"]["ood_gain"]
    if unreg_highest and uo["lo"] >= -0.03 and uo["hi"] <= 0.03:
        l1 = "REPRODUCED"
    elif uo["mean"] >= summ["full"]["ood_gain"]["mean"]:
        l1 = "NOT REPRODUCED"
    elif unreg_highest and abs(uo["mean"]) <= 0.03:
        l1 = "PARTIAL (OOD point estimate within +-3 pts, CI not)"
    else:
        l1 = "PARTIAL"
    c7 = ("CONTRADICTED" if (summ["budget-only"]["ood_gain"]["hi"] < 0.5 * summ["full"]["ood_gain"]["mean"]
                             and sel_minus_budget["ood_gain"]["lo"] > 0) else "not contradicted")
    m35 = {lab: overfits(vs_full[lab]) for lab in ("unregularized", "unregularized-argmax", "unregularized-b8")}
    return {"L4": {"verdict": l4, "primary_ood_lower": ood_p, "primary_evolve_noninferior": evo_p,
                   "S1_ood_lower": ood_s, "S1_evolve_noninferior": evo_s},
            "L3": {"verdict": l3, "per_arm": l3_arms},
            "L1": {"verdict": l1, "unregularized_highest_evolve": unreg_highest, "unregularized_ood_gain": uo},
            "C7": {"verdict": c7, "budget_only_ood": summ["budget-only"]["ood_gain"],
                   "half_full_ood": 0.5 * summ["full"]["ood_gain"]["mean"], "selector_minus_budget": sel_minus_budget},
            "M35_robustness": {"all_variants_overfit": all(m35.values()), "per_variant": m35}}


def main():
    a = retry_args("E2c: ablations at the workspace preset", T=None)
    cfg = dict(WORKSPACE)
    if a.T:
        cfg["T"] = a.T
    t0 = time.time()
    jobs = [{"seed": s, "arm": sw, "label": lab, "cfg": cfg, **({"proposer": prop} if prop else {})}
            for s in range(a.seeds) for lab, (sw, prop) in ARMS.items()]
    rows = pmap(run_hw, jobs, a.workers)
    summ = summarize(rows, metrics=("measured_gain", "evolve_gain", "holdout_gain", "ood_gain", "unseen_gain",
                                    "token_ratio", "n_accepted", "hitchhiker_rate", "leaks_in_final", "n_mechanisms",
                                    "delta"))
    vs_full = {lab: pairs(rows, "full", lab, PM) for lab in ARMS if lab not in ("full", "S1 full", "S1 -proposal")}
    s1 = pairs(rows, "S1 full", "S1 -proposal", PM)
    tests = preregistered_tests(summ, vs_full, s1, pairs(rows, "budget-only", "-proposal", ("ood_gain", "unseen_gain")))
    table = [{"arm": lab, "measured_evolve": fmt(s["measured_gain"]), "true_evolve": fmt(s["evolve_gain"]),
              "holdout": fmt(s["holdout_gain"]), "ood": fmt(s["ood_gain"]), "tokens_x_H0": fmt_ratio(s["token_ratio"]),
              "leaks_in_final": round(s["leaks_in_final"]["mean"], 2),
              "n_mechanisms": round(s["n_mechanisms"]["mean"], 1)}
             for lab, s in summ.items()]
    out = {"experiment": "E2c ablations at the workspace preset (retry round 2: P4)",
           "config": {"seeds": a.seeds, "cfg": cfg, "arms": {k: v[0].to_json() for k, v in ARMS.items()},
                      "S1_proposer": COLLAPSE},
           "table": table, "summary": summ, "paired_vs_full": vs_full, "S1_minus_proposal_vs_full": s1,
           "tests": tests, "wall_s": round(time.time() - t0, 1), "rows": strip_curves(rows)}
    save("e2c_ablations_workspace", out)
    for r in table:
        print(r)
    for lab, v in vs_full.items():
        print("vs full:", lab, {m: ci_pts(x, 1 if m == "token_ratio" else 100) for m, x in v.items()})
    print("S1 -proposal - full:", {m: ci_pts(x) for m, x in s1.items()})
    for k, v in tests.items():
        print(k, v)


if __name__ == "__main__":
    main()
