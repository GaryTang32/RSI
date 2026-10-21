"""E12: evolved harnesses transfer to a weaker policy never used in the search (paper Tables 3-4).

Run: python experiments/rrsi/e12_weak_policy.py [--quick] [--seeds N]
"""
from _common import agentqa_rows, figure, pairs, parse_args, pmap, run_hw, save, strip_curves, summarize, verdict


def main():
    a = parse_args("E12: transfer to a weaker policy never used in the search", default_seeds=50)
    T = 8 if a.quick else 20
    arms = ["unregularized", "full"]
    jobs = [{"seed": s, "arm": arm, "label": arm, "cfg": {"T": T}, "weak": True, "llm": a.llm}
            for s in range(a.seeds) for arm in arms]
    rows = pmap(run_hw, jobs, a.procs)
    metrics = ("weak_holdout_gain", "weak_ood_gain", "weak_unseen_gain", "holdout_gain", "ood_gain", "token_ratio")
    hw = summarize(rows, metrics=metrics)
    hw_pd = pairs(rows, "unregularized", "full", metrics[:3])
    _, aq_rows = agentqa_rows(a, arms, weak_skill=0.6)
    aq = summarize(aq_rows, metrics=("weak_holdout_gain", "weak_ood_gain", "weak_unseen_gain", "holdout_gain",
                                     "ood_gain"))
    checks = {"harnessworld: RRSI harness transfers positively to the weak policy":
                  hw["full"]["weak_unseen_gain"]["lo"] > 0,
              "harnessworld: unregularized transfers less": hw_pd["weak_unseen_gain"]["lo"] > 0,
              "agentqa: RRSI harness transfers positively to SimModel(skill=0.6)":
                  aq["full"]["weak_unseen_gain"]["mean"] > 0}
    out = {"experiment": "E12 weak-policy transfer",
           "config": {"T": T, "seeds": a.seeds,
                      "weak_policy": "WEAK (strength -1.0; subagent -0.5, skill 0.5, client_tool 1.3)",
                      "agentqa_weak": "SimModel(skill=0.6)", "llm": a.llm},
           "harnessworld": {"summary": hw, "paired_full_minus_unregularized": hw_pd},
           "agentqa": {"summary": aq, "rows": aq_rows}, "checks": checks, "verdict": verdict(checks),
           "rows": strip_curves(rows)}
    save("e12_weak_policy", out)

    def draw(ax):
        for i, (name, s) in enumerate((("HarnessWorld (weak policy)", hw), ("AgentQA (SimModel skill 0.6)", aq))):
            for j, arm in enumerate(arms):
                ax[i].bar([x + 0.4 * j for x in range(2)],
                          [s[arm][m]["mean"] * 100 for m in ("weak_holdout_gain", "weak_ood_gain")], width=0.4,
                          label=arm)
            ax[i].set_xticks([0.2, 1.2])
            ax[i].set_xticklabels(["held-out", "OOD"])
            ax[i].set_ylabel("gain over H_0 on the weak policy (points)")
            ax[i].set_title(name)
            ax[i].legend()
    figure("e12_weak_policy", draw, 2, figsize=(11, 4))
    print({k: {m: round(v[m]["mean"], 4) for m in metrics} for k, v in hw.items()})
    print({k: {m: round(v[m]["mean"], 4) for m in v} for k, v in aq.items()})
    print(out["verdict"])


if __name__ == "__main__":
    main()
