"""E2 (+E2b): the regularizer-group ablation of the paper's Table 2 on HarnessWorld, candidate- and token-matched.

Run: python experiments/rrsi/e2_ablations.py [--quick] [--seeds N]
"""
from _common import (figure, fmt, fmt_ratio, pairs, parse_args, pmap, run_hw, save, strip_curves, summarize,
                     verdict)

from rsi.rrsi import RegularizerSwitches

ARMS = {
    "no guards (unregularized)": RegularizerSwitches.none(),
    "without proposal guards": RegularizerSwitches.no_proposal(),
    "without acceptance guards": RegularizerSwitches.no_acceptance(),
    "full RRSI": RegularizerSwitches.full(),
    "E2b budget-only": RegularizerSwitches.budget_only(),
    "E2b selector-only": RegularizerSwitches.selector_only(),
    "sens: -proposal (pruning counted as proposal)": RegularizerSwitches.no_proposal(prune_in_proposal_group=True),
    "sens: -acceptance (pruning counted as proposal)": RegularizerSwitches.no_acceptance(prune_in_proposal_group=True),
}
# practised, held-out, unseen (pts), tokens (M)
PAPER = {"no guards (unregularized)": (92.8, 88.9, 40.3, 3.80), "without proposal guards": (90.7, 88.8, 41.9, 2.69),
         "without acceptance guards": (91.5, 88.7, 41.0, 3.59), "full RRSI": (90.5, 89.2, 43.6, 2.42),
         "H_0": (89.4, 86.9, 39.7, 1.56)}
FULL = "full RRSI"
MAIN = ["no guards (unregularized)", "without proposal guards", "without acceptance guards"]


def label_mean(rows: list, lab: str, get) -> float:
    vals = [get(r) for r in rows if r["label"] == lab]
    return sum(vals) / max(1, len(vals))


def table_row(lab: str, s: dict) -> dict:
    return {"variant": lab, "measured_evolve_pts": fmt(s["measured_gain"]), "true_evolve_pts": fmt(s["evolve_gain"]),
            "true_heldout_pts": fmt(s["holdout_gain"]), "true_ood_pts": fmt(s["ood_gain"]),
            "tokens_x_H0": fmt_ratio(s["token_ratio"]), "paper (practised, held-out, unseen, tokens M)": PAPER.get(lab)}


def main():
    a = parse_args("E2: regularizer-group ablations", default_seeds=50)
    T = 8 if a.quick else 20
    jobs = [{"seed": s, "arm": sw, "label": lab, "cfg": {"T": T}, "llm": a.llm}
            for s in range(a.seeds) for lab, sw in ARMS.items()]
    rows = pmap(run_hw, jobs, a.procs)
    # E2b token matching: critic-free arms get more rounds until their search-LLM tokens match full RRSI's
    tok = {lab: label_mean(rows, lab, lambda r: r["search_tokens"]) for lab in ARMS}
    matched = {}
    for lab in ("no guards (unregularized)", "E2b budget-only"):
        ratio = tok[FULL] / max(1.0, tok[lab])
        T_m = max(T, min(4 * T, int(round(T * ratio))))
        matched[f"{lab} [token-matched, T={T_m}]"] = (ARMS[lab], T_m, ratio)
    rows += pmap(run_hw, [{"seed": s, "arm": sw, "label": lab, "cfg": {"T": T_m}, "llm": a.llm}
                          for s in range(a.seeds) for lab, (sw, T_m, _) in matched.items()], a.procs)
    labels = list(ARMS) + list(matched)
    summ = summarize(rows, metrics=("measured_gain", "evolve_gain", "holdout_gain", "ood_gain", "unseen_gain",
                                    "token_ratio", "n_accepted", "hitchhiker_rate", "leaks_in_final", "n_mechanisms",
                                    "n_rollouts", "search_tokens"))
    vs_full = {lab: pairs(rows, FULL, lab, ("measured_gain", "evolve_gain", "holdout_gain", "ood_gain", "token_ratio"))
               for lab in labels if lab != FULL}
    compute = {lab: {"rollouts": summ[lab]["n_rollouts"]["mean"], "search_tokens": summ[lab]["search_tokens"]["mean"],
                     "proposer_calls": label_mean(rows, lab, lambda r: r["usage_calls"].get("proposer") or 0),
                     "critic_calls": label_mean(rows, lab, lambda r: r["usage_calls"].get("critic") or 0)}
               for lab in summ}
    checks = {}
    for lab in MAIN:
        checks[f"{lab}: raises measured evolve"] = vs_full[lab]["measured_gain"]["lo"] > 0
        checks[f"{lab}: lowers true OOD"] = vs_full[lab]["ood_gain"]["hi"] < 0
        checks[f"{lab}: more tokens"] = vs_full[lab]["token_ratio"]["lo"] > 0
    ng = summ["no guards (unregularized)"]
    checks["no guards worst on OOD"] = ng["ood_gain"]["mean"] == min(summ[x]["ood_gain"]["mean"] for x in MAIN + [FULL])
    checks["no guards most expensive"] = ng["token_ratio"]["mean"] == max(summ[x]["token_ratio"]["mean"]
                                                                          for x in MAIN + [FULL])
    table = [table_row(lab, summ[lab]) for lab in labels]
    bo_tm = next(k for k in matched if k.startswith("E2b budget-only"))
    budget_vs_selector = pairs(rows, "E2b budget-only", "E2b selector-only", ("ood_gain", "unseen_gain"))
    budget_vs_selector_tm = pairs(rows, bo_tm, "E2b selector-only", ("ood_gain", "unseen_gain"))
    # E2b is an open question, not a paper claim: reported, not part of the verdict
    e2b = {"selector-only beats budget-only on OOD (candidate-matched)": budget_vs_selector["ood_gain"]["lo"] > 0,
           "selector-only beats budget-only on OOD (search-token-matched)": budget_vs_selector_tm["ood_gain"]["lo"] > 0,
           "budget-only ~ full on OOD (the third-party hypothesis)":
               vs_full["E2b budget-only"]["ood_gain"]["lo"] > -0.02}
    out = {"experiment": "E2 ablations (+E2b)", "config": {"T": T, "seeds": a.seeds, "llm": a.llm,
                                                           "arms": {k: v.to_json() for k, v in ARMS.items()}},
           "matching": {"candidate_matched": "all arms in 'config.arms': same T x m drafts and evaluation rollouts",
                        "token_matched": {k: {"T": v[1], "search_token_ratio_full_over_arm_at_T": v[2]}
                                          for k, v in matched.items()}},
           "compute": compute,
           "table": table, "summary": summ, "paired_vs_full": vs_full, "E2b_selector_minus_budget": budget_vs_selector,
           "E2b_selector_minus_budget_token_matched": budget_vs_selector_tm,
           "note": "'E2b selector-only' uses exactly the switches of 'without proposal guards' (same seeds), so the "
                   "two rows coincide by construction.",
           "checks": checks, "E2b_findings": e2b, "verdict": verdict(checks), "rows": strip_curves(rows)}
    save("e2_ablations", out)

    def draw(ax):
        for lab in ARMS:
            tr, ood = summ[lab]["token_ratio"], summ[lab]["ood_gain"]
            ax[0].errorbar(tr["mean"], ood["mean"] * 100, xerr=[[tr["mean"] - tr["lo"]], [tr["hi"] - tr["mean"]]],
                           yerr=[[(ood["mean"] - ood["lo"]) * 100], [(ood["hi"] - ood["mean"]) * 100]], fmt="o",
                           label=lab)
        ax[0].set_xscale("log")
        ax[0].set_xlabel("tokens per trial / H_0")
        ax[0].set_ylabel("true OOD gain (points)")
        ax[0].legend(fontsize=7)
        ax[0].set_title("OOD vs cost (Fig. 4 analogue)")
        w = 0.2
        for j, m in enumerate(["measured_gain", "holdout_gain", "ood_gain"]):
            ax[1].bar([i + w * j for i in range(4)], [summ[lab][m]["mean"] * 100 for lab in list(ARMS)[:4]], width=w,
                      label=m)
        ax[1].set_xticks([i + w for i in range(4)])
        ax[1].set_xticklabels(["no guards", "-proposal", "-acceptance", "full"])
        ax[1].set_ylabel("gain over H_0 (points)")
        ax[1].legend(fontsize=8)
        ax[1].set_title("Ablation table")
    figure("e2_ablations", draw, 2, figsize=(13, 4.5))
    for row in table:
        print(row)
    print(out["verdict"])


if __name__ == "__main__":
    main()
