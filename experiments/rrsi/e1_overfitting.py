"""E1 - Unregularized evolution overfits; RRSI transfers (paper Tables 1-2, overview "ranking flips").

HarnessWorld (Tier 1, analytic ground truth): H_0 vs unregularized evolution vs full RRSI,
same proposer, same budget (T = 20 rounds x m = 2 candidates), N seeds (a fresh world per
seed). Measures the loop's own final measured evolve score, true E[S] on evolve / ID
held-out / OOD, and tokens per trial; paired (same seed) differences with bootstrap CIs.
A robustness arm switches the world's context-dilution assumption off.

AgentQA (the shared harness domain, SimModel as the frozen model, scripted mock
proposer): the same two arms on 5 suite seeds, transfer measured on held-out and
never-seen question families.

Confirming outcome (spec E1): unregularized has the highest evolve score; RRSI has a
lower evolve score but higher true ID and OOD; the unregularized OOD gain is near 0.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import (RESULTS, fmt, mean_curves, paired, parse_args, pmap, run_aq, run_hw, save,  # noqa: E402
                     strip_curves, summarize)


def main():
    a = parse_args("E1: overfitting of unregularized evolution vs RRSI", default_seeds=50)
    T = 8 if a.quick else 20
    arms = ["unregularized", "full"]
    jobs = [{"seed": s, "arm": arm, "label": arm, "cfg": {"T": T}, "llm": a.llm} for s in range(a.seeds) for arm in arms]
    n_rob = min(a.seeds, 20)
    jobs += [{"seed": s, "arm": arm, "label": f"{arm}|no_context_penalty", "cfg": {"T": T}, "llm": a.llm,
              "world": {"context_penalty": 0.0}} for s in range(n_rob) for arm in arms]
    rows = pmap(run_hw, jobs, a.workers if a.llm == "sim" else 1)
    hw = summarize(rows)
    pd = {m: paired(rows, "unregularized", "full", m) for m in
          ("measured_gain", "evolve_gain", "holdout_gain", "ood_gain", "unseen_gain", "token_ratio")}
    pd_rob = {m: paired(rows, "unregularized|no_context_penalty", "full|no_context_penalty", m) for m in
              ("evolve_gain", "holdout_gain", "ood_gain", "token_ratio")}
    # AgentQA second domain
    aq_seeds = min(5, a.seeds) if a.llm == "sim" else 1
    aq_jobs = [{"seed": s, "arm": arm, "label": arm, "cfg": {"T": 8 if not a.quick else 4}, "llm": a.llm}
               for s in range(aq_seeds) for arm in arms]
    aq_rows = pmap(run_aq, aq_jobs, min(2, a.workers) if a.llm == "sim" else 1)
    aq = summarize(aq_rows)
    aq_pd = {m: paired(aq_rows, "unregularized", "full", m) for m in ("measured_gain", "holdout_gain", "ood_gain")}
    u, f = hw["unregularized"], hw["full"]
    checks = {
        "unregularized_highest_measured_evolve": u["measured_gain"]["mean"] > f["measured_gain"]["mean"] and pd["measured_gain"]["hi"] < 0,
        "unregularized_highest_true_evolve": pd["evolve_gain"]["hi"] < 0,
        "rrsi_higher_true_holdout": pd["holdout_gain"]["lo"] > 0,
        "rrsi_higher_true_ood": pd["ood_gain"]["lo"] > 0,
        "unregularized_ood_gain_near_zero": abs(u["ood_gain"]["mean"]) < 0.03,
        "rrsi_cheaper": pd["token_ratio"]["hi"] < 0,
    }
    out = {
        "experiment": "E1 H0 vs unregularized vs RRSI",
        "config": {"domain": "harnessworld (default WorldConfig)", "T": T, "m": 2, "seeds": a.seeds, "llm": a.llm,
                   "rrsi_config": "Config() defaults (paper appendix); delta calibrated by bootstrap",
                   "unregularized": "b_t = b_max, accepted-only history, no exploration, no critic (smoke only), "
                                    "keep argmax iff S' > S_t",
                   "agentqa": {"seeds": aq_seeds, "T": 8, "task_model": "SimModel", "proposer": a.llm}},
        "harnessworld": {"summary": hw, "paired_full_minus_unregularized": pd,
                         "robustness_no_context_penalty": {"summary": {k: v for k, v in hw.items() if "|" in k},
                                                           "paired": pd_rob},
                         "curves": mean_curves([r for r in rows if "|" not in r["label"]])},
        "agentqa": {"summary": aq, "paired_full_minus_unregularized": aq_pd,
                    "leaky_final": {arm: sum(r["leaky_final"] for r in aq_rows if r["arm"] == arm) for arm in arms},
                    "rows": aq_rows},
        "table_points": {lab: {m: fmt(hw[lab][m]) if m != "token_ratio" else f"{hw[lab][m]['mean']:.2f}x"
                               for m in hw[lab]} for lab in hw},
        "checks": checks,
        "verdict": ("REPRODUCED" if all(checks.values()) else "PARTIAL: " + ", ".join(k for k, v in checks.items() if not v)
                    + " not met"),
        # the pre-declared 'near zero' check is two-sided; say which side a miss is on
        "interpretation": {"unregularized_ood_gain": fmt(u["ood_gain"]) + " pts: " + (
            "significantly BELOW H_0 (a miss of 'near zero' in the direction of more overfitting)"
            if u["ood_gain"]["hi"] < 0 else "significantly ABOVE H_0 (some transfer)" if u["ood_gain"]["lo"] > 0
            else "not distinguishable from H_0"),
            "agentqa_tokens": {arm: f"{aq[arm]['token_ratio']['mean']:.2f}x [{aq[arm]['token_ratio']['lo']:.2f}, "
                                    f"{aq[arm]['token_ratio']['hi']:.2f}]" for arm in arms}},
        "rows": strip_curves(rows),
    }
    save("e1_overfitting", out)
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        cur = out["harnessworld"]["curves"]
        fig, ax = plt.subplots(1, 3, figsize=(15, 4.2))
        col = {"unregularized": "#d9730d", "full": "#2f6fdf"}
        for lab, c in cur.items():
            base = c["evolve"][0]
            ax[0].plot([x - c["measured"][0] for x in c["measured"]], ":", color=col[lab], label=f"{lab}: measured evolve")
            ax[0].plot([x - base for x in c["evolve"]], "--", color=col[lab], label=f"{lab}: true evolve")
            ax[0].plot([x - c["holdout"][0] for x in c["holdout"]], "-", color=col[lab], alpha=0.6, label=f"{lab}: true held-out")
            ax[0].plot([x - c["ood"][0] for x in c["ood"]], "-", color=col[lab], lw=2.5, label=f"{lab}: true OOD")
            ax[1].plot([x / c["tokens"][0] for x in c["tokens"]], color=col[lab], label=lab)
        ax[0].set_xlabel("round")
        ax[0].set_ylabel("score change vs H_0")
        ax[0].legend(fontsize=7)
        ax[0].set_title(f"HarnessWorld, mean of {a.seeds} seeds")
        ax[1].set_yscale("log")
        ax[1].set_xlabel("round")
        ax[1].set_ylabel("tokens per trial / H_0")
        ax[1].legend(fontsize=8)
        ax[1].set_title("Complexity accumulation")
        labs = ["measured_gain", "evolve_gain", "holdout_gain", "ood_gain"]
        for j, lab in enumerate(["unregularized", "full"]):
            ax[2].bar([i + 0.4 * j for i in range(4)], [hw[lab][m]["mean"] * 100 for m in labs], width=0.4,
                      color=col[lab], label=lab,
                      yerr=[[(hw[lab][m]["mean"] - hw[lab][m]["lo"]) * 100 for m in labs],
                            [(hw[lab][m]["hi"] - hw[lab][m]["mean"]) * 100 for m in labs]])
        ax[2].set_xticks([i + 0.2 for i in range(4)])
        ax[2].set_xticklabels(["measured\nevolve", "true\nevolve", "true\nheld-out", "true\nOOD"])
        ax[2].set_ylabel("gain over H_0 (points)")
        ax[2].legend()
        ax[2].set_title("Final harness (95% CI)")
        fig.tight_layout()
        fig.savefig(RESULTS / "e1_overfitting.png", dpi=110)
    except Exception as e:  # noqa: BLE001
        print("figure skipped:", e)
    print(out["table_points"])
    print(out["checks"], out["verdict"])
    print("agentqa", {k: {m: round(v[m]["mean"], 3) for m in ("measured_gain", "holdout_gain", "ood_gain", "token_ratio")}
                      for k, v in aq.items()})


if __name__ == "__main__":
    main()
