"""E2 (+E2b) - ablation table mirroring the paper's Table 2 (overview "What each group of guards contributes").

Arms on HarnessWorld (same proposer / budget / seeds): H_0, no guards at all (unregularized
evolution), without proposal guards, without acceptance guards, full RRSI; plus the
compute-matched E2b arms (budget-only, selector-only) that probe the third-party claim
that the annealed budget alone explains most of the OOD gain, and a sensitivity variant
that counts pruning in the proposal group instead of the acceptance group.

Paper (workspace): unregularized 92.8 / 88.9 / 40.3 / 3.80M; -proposal 90.7 / 88.8 / 41.9 /
2.69M; -acceptance 91.5 / 88.7 / 41.0 / 3.59M; full 90.5 / 89.2 / 43.6 / 2.42M
(practised / held-out / unseen / tokens). Claim: removing either group raises the
practised score and lowers the unseen one; no guards is worst on OOD and most expensive.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import RESULTS, fmt, paired, parse_args, pmap, run_hw, save, strip_curves, summarize  # noqa: E402

from rsi.rrsi import RegularizerSwitches  # noqa: E402

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
PAPER = {"no guards (unregularized)": (92.8, 88.9, 40.3, 3.80), "without proposal guards": (90.7, 88.8, 41.9, 2.69),
         "without acceptance guards": (91.5, 88.7, 41.0, 3.59), "full RRSI": (90.5, 89.2, 43.6, 2.42),
         "H_0": (89.4, 86.9, 39.7, 1.56)}


def main():
    a = parse_args("E2: regularizer-group ablations", default_seeds=50)
    T = 8 if a.quick else 20
    jobs = [{"seed": s, "arm": sw, "label": lab, "cfg": {"T": T}, "llm": a.llm}
            for s in range(a.seeds) for lab, sw in ARMS.items()]
    rows = pmap(run_hw, jobs, a.workers if a.llm == "sim" else 1)
    metrics = ("measured_gain", "evolve_gain", "holdout_gain", "ood_gain", "unseen_gain", "token_ratio",
               "n_accepted", "hitchhiker_rate", "leaks_in_final", "n_mechanisms")
    summ = summarize(rows, metrics=metrics)
    full = "full RRSI"
    vs_full = {lab: {m: paired(rows, full, lab, m) for m in ("measured_gain", "evolve_gain", "holdout_gain", "ood_gain",
                                                             "token_ratio")} for lab in ARMS if lab != full}
    main_arms = ["no guards (unregularized)", "without proposal guards", "without acceptance guards"]
    checks = {}
    for lab in main_arms:
        checks[f"{lab}: raises measured evolve"] = vs_full[lab]["measured_gain"]["lo"] > 0
        checks[f"{lab}: lowers true OOD"] = vs_full[lab]["ood_gain"]["hi"] < 0
        checks[f"{lab}: more tokens"] = vs_full[lab]["token_ratio"]["lo"] > 0
    checks["no guards worst on OOD"] = summ["no guards (unregularized)"]["ood_gain"]["mean"] == min(
        summ[l]["ood_gain"]["mean"] for l in main_arms + [full])
    checks["no guards most expensive"] = summ["no guards (unregularized)"]["token_ratio"]["mean"] == max(
        summ[l]["token_ratio"]["mean"] for l in main_arms + [full])
    table = []
    for lab in ARMS:
        s = summ[lab]
        table.append({"variant": lab, "measured_evolve_pts": fmt(s["measured_gain"]), "true_evolve_pts": fmt(s["evolve_gain"]),
                      "true_heldout_pts": fmt(s["holdout_gain"]), "true_ood_pts": fmt(s["ood_gain"]),
                      "tokens_x_H0": f"{s['token_ratio']['mean']:.2f} [{s['token_ratio']['lo']:.2f}, {s['token_ratio']['hi']:.2f}]",
                      "paper (practised, held-out, unseen, tokens M)": PAPER.get(lab)})
    budget_vs_selector = {m: paired(rows, "E2b budget-only", "E2b selector-only", m) for m in ("ood_gain", "unseen_gain")}
    out = {"experiment": "E2 ablations (+E2b)", "config": {"T": T, "seeds": a.seeds, "llm": a.llm,
                                                           "arms": {k: v.to_json() for k, v in ARMS.items()}},
           "table": table, "summary": summ, "paired_vs_full": vs_full, "E2b_selector_minus_budget": budget_vs_selector,
           "checks": checks,
           "verdict": ("REPRODUCED" if all(checks.values()) else
                       "PARTIAL - not met: " + "; ".join(k for k, v in checks.items() if not v)),
           "rows": strip_curves(rows)}
    save("e2_ablations", out)
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(1, 2, figsize=(13, 4.5))
        labs = list(ARMS)
        for i, lab in enumerate(labs):
            s = summ[lab]
            ax[0].errorbar(s["token_ratio"]["mean"], s["ood_gain"]["mean"] * 100,
                           xerr=[[s["token_ratio"]["mean"] - s["token_ratio"]["lo"]], [s["token_ratio"]["hi"] - s["token_ratio"]["mean"]]],
                           yerr=[[(s["ood_gain"]["mean"] - s["ood_gain"]["lo"]) * 100], [(s["ood_gain"]["hi"] - s["ood_gain"]["mean"]) * 100]],
                           fmt="o", label=lab)
        ax[0].set_xscale("log")
        ax[0].set_xlabel("tokens per trial / H_0")
        ax[0].set_ylabel("true OOD gain (points)")
        ax[0].legend(fontsize=7)
        ax[0].set_title("OOD vs cost (Fig. 4 analogue)")
        w = 0.2
        for j, m in enumerate(["measured_gain", "holdout_gain", "ood_gain"]):
            ax[1].bar([i + w * j for i in range(4)], [summ[l][m]["mean"] * 100 for l in labs[:4]], width=w, label=m)
        ax[1].set_xticks([i + w for i in range(4)])
        ax[1].set_xticklabels(["no guards", "-proposal", "-acceptance", "full"])
        ax[1].set_ylabel("gain over H_0 (points)")
        ax[1].legend(fontsize=8)
        ax[1].set_title("Ablation table")
        fig.tight_layout()
        fig.savefig(RESULTS / "e2_ablations.png", dpi=110)
    except Exception as e:  # noqa: BLE001
        print("figure skipped:", e)
    for row in table:
        print(row)
    print(out["verdict"])


if __name__ == "__main__":
    main()
