"""E1: unregularized evolution overfits, RRSI transfers (paper Tables 1-2), on HarnessWorld and AgentQA.

Run: python experiments/rrsi/e1_overfitting.py [--quick] [--seeds N] [--llm sim|claude:haiku]
"""
from _common import (METRICS, agentqa_rows, figure, fmt, fmt_ratio, mean_curves, pairs, parse_args, pmap, run_hw,
                     save, strip_curves, summarize)


def main():
    a = parse_args("E1: overfitting of unregularized evolution vs RRSI", default_seeds=50)
    T = 8 if a.quick else 20
    arms = ["unregularized", "full"]
    n_rob = min(a.seeds, 20)
    # main arms; robustness: no context-dilution penalty; the three-lens LLM analyst instead of the heuristic one
    variants = (("", a.seeds, {}, {}), ("|no_context_penalty", n_rob, {}, {"context_penalty": 0.0}),
                ("|llm_analyst", n_rob, {"analyst": "llm"}, {}))
    jobs = [{"seed": s, "arm": arm, "label": arm + sfx, "cfg": {"T": T, **cfg}, "llm": a.llm, "world": world}
            for sfx, n, cfg, world in variants for s in range(n) for arm in arms]
    rows = pmap(run_hw, jobs, a.procs)
    hw = summarize(rows)
    pd = pairs(rows, "unregularized", "full", METRICS)
    pd_rob = pairs(rows, "unregularized|no_context_penalty", "full|no_context_penalty",
                   ("evolve_gain", "holdout_gain", "ood_gain", "token_ratio"))
    pd_llm = pairs(rows, "unregularized|llm_analyst", "full|llm_analyst",
                   ("measured_gain", "evolve_gain", "holdout_gain", "ood_gain", "token_ratio"))
    analyst_modes = {lab: sorted({str(r.get("analyst_mode")) for r in rows if r["label"] == lab})
                     for lab in dict.fromkeys(r["label"] for r in rows)}
    aq_seeds, aq_rows = agentqa_rows(a, arms)
    aq = summarize(aq_rows)
    aq_pd = pairs(aq_rows, "unregularized", "full", ("measured_gain", "holdout_gain", "ood_gain"))
    u, f = hw["unregularized"], hw["full"]
    checks = {
        "unregularized_highest_measured_evolve": u["measured_gain"]["mean"] > f["measured_gain"]["mean"]
                                                 and pd["measured_gain"]["hi"] < 0,
        "unregularized_highest_true_evolve": pd["evolve_gain"]["hi"] < 0,
        "rrsi_higher_true_holdout": pd["holdout_gain"]["lo"] > 0,
        "rrsi_higher_true_ood": pd["ood_gain"]["lo"] > 0,
        "unregularized_ood_gain_near_zero": abs(u["ood_gain"]["mean"]) < 0.03,
        "rrsi_cheaper": pd["token_ratio"]["hi"] < 0,
    }
    uo = u["ood_gain"]
    out = {
        "experiment": "E1 H0 vs unregularized vs RRSI",
        "config": {"domain": "harnessworld (default WorldConfig)", "T": T, "m": 2, "seeds": a.seeds, "llm": a.llm,
                   "rrsi_config": "Config() defaults (paper appendix); delta calibrated by bootstrap",
                   "unregularized": "b_t = b_max, accepted-only history, no exploration, no critic (smoke only), "
                                    "keep argmax iff S' > S_t",
                   "agentqa": {"seeds": aq_seeds, "T": 8, "task_model": "SimModel", "proposer": a.llm}},
        "harnessworld": {"summary": hw, "paired_full_minus_unregularized": pd,
                         "analyst_mode_per_arm": analyst_modes,
                         "robustness_no_context_penalty": {
                             "summary": {k: v for k, v in hw.items() if k.endswith("|no_context_penalty")},
                             "paired": pd_rob},
                         "robustness_llm_analyst": {
                             "summary": {k: v for k, v in hw.items() if k.endswith("|llm_analyst")},
                             "paired": pd_llm,
                             "checks": {"rrsi_higher_true_ood": pd_llm["ood_gain"]["lo"] > 0,
                                        "rrsi_cheaper": pd_llm["token_ratio"]["hi"] < 0,
                                        "unregularized_highest_true_evolve": pd_llm["evolve_gain"]["hi"] < 0}},
                         "curves": mean_curves([r for r in rows if "|" not in r["label"]])},
        "agentqa": {"summary": aq, "paired_full_minus_unregularized": aq_pd,
                    "analyst_mode": sorted({str(r.get("analyst_mode")) for r in aq_rows}),
                    "leaky_final": {arm: sum(r["leaky_final"] for r in aq_rows if r["arm"] == arm) for arm in arms},
                    "rows": aq_rows},
        "table_points": {lab: {m: fmt(hw[lab][m]) if m != "token_ratio" else f"{hw[lab][m]['mean']:.2f}x"
                               for m in hw[lab]} for lab in hw},
        "checks": checks,
        "verdict": "REPRODUCED" if all(checks.values()) else
                   "PARTIAL: " + ", ".join(k for k, v in checks.items() if not v) + " not met",
        # 'near zero' is two-sided: say which side a miss is on
        "interpretation": {"unregularized_ood_gain": fmt(uo) + " pts: " + (
            "significantly BELOW H_0 (a miss of 'near zero' in the direction of more overfitting)"
            if uo["hi"] < 0 else "significantly ABOVE H_0 (some transfer)" if uo["lo"] > 0
            else "not distinguishable from H_0"),
            "agentqa_tokens": {arm: fmt_ratio(aq[arm]["token_ratio"], "x") for arm in arms}},
        "rows": strip_curves(rows),
    }
    save("e1_overfitting", out)

    def draw(ax):
        col = {"unregularized": "#d9730d", "full": "#2f6fdf"}
        for lab, c in out["harnessworld"]["curves"].items():
            ax[0].plot([x - c["measured"][0] for x in c["measured"]], ":", color=col[lab],
                       label=f"{lab}: measured evolve")
            ax[0].plot([x - c["evolve"][0] for x in c["evolve"]], "--", color=col[lab], label=f"{lab}: true evolve")
            ax[0].plot([x - c["holdout"][0] for x in c["holdout"]], "-", color=col[lab], alpha=0.6,
                       label=f"{lab}: true held-out")
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
        ms = ["measured_gain", "evolve_gain", "holdout_gain", "ood_gain"]
        for j, lab in enumerate(arms):
            s = hw[lab]
            ax[2].bar([i + 0.4 * j for i in range(4)], [s[m]["mean"] * 100 for m in ms], width=0.4, color=col[lab],
                      label=lab, yerr=[[(s[m]["mean"] - s[m]["lo"]) * 100 for m in ms],
                                       [(s[m]["hi"] - s[m]["mean"]) * 100 for m in ms]])
        ax[2].set_xticks([i + 0.2 for i in range(4)])
        ax[2].set_xticklabels(["measured\nevolve", "true\nevolve", "true\nheld-out", "true\nOOD"])
        ax[2].set_ylabel("gain over H_0 (points)")
        ax[2].legend()
        ax[2].set_title("Final harness (95% CI)")
    figure("e1_overfitting", draw, 3, figsize=(15, 4.2))
    print(out["table_points"])
    print(out["checks"], out["verdict"])
    print("agentqa", {k: {m: round(v[m]["mean"], 3) for m in ("measured_gain", "holdout_gain", "ood_gain",
                                                               "token_ratio")} for k, v in aq.items()})


if __name__ == "__main__":
    main()
