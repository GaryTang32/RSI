"""E12 - Mechanisms transfer across policies (paper Tables 3-4: the Gemini-3.5-Flash-evolved
harness run unchanged on Gemini 3.1 Flash Lite, never used in the search).

HarnessWorld: harnesses evolved with the strong policy (RRSI vs unregularized) are run
unchanged by a WEAK policy (lower base skill; gains more from tools, less from textual
skills, hurt by sub-calls) on held-out and OOD tasks; gains vs H_0 under the same weak
policy. AgentQA: harnesses evolved with SimModel(skill = 1.0) are run by SimModel(skill =
0.6).

Confirming outcome (spec E12): positive transfer for RRSI; smaller or no gain for the
unregularized harness.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import RESULTS, paired, parse_args, pmap, run_aq, run_hw, save, strip_curves, summarize  # noqa: E402


def main():
    a = parse_args("E12: transfer to a weaker policy never used in the search", default_seeds=50)
    T = 8 if a.quick else 20
    arms = ["unregularized", "full"]
    jobs = [{"seed": s, "arm": arm, "label": arm, "cfg": {"T": T}, "weak": True, "llm": a.llm}
            for s in range(a.seeds) for arm in arms]
    rows = pmap(run_hw, jobs, a.workers if a.llm == "sim" else 1)
    metrics = ("weak_holdout_gain", "weak_ood_gain", "weak_unseen_gain", "holdout_gain", "ood_gain", "token_ratio")
    hw = summarize(rows, metrics=metrics)
    hw_pd = {m: paired(rows, "unregularized", "full", m) for m in metrics[:3]}
    aq_seeds = min(5, a.seeds) if a.llm == "sim" else 1
    aq_rows = pmap(run_aq, [{"seed": s, "arm": arm, "label": arm, "cfg": {"T": 8 if not a.quick else 4},
                             "weak_skill": 0.6, "llm": a.llm} for s in range(aq_seeds) for arm in arms],
                   min(2, a.workers) if a.llm == "sim" else 1)
    aq = summarize(aq_rows, metrics=("weak_holdout_gain", "weak_ood_gain", "weak_unseen_gain", "holdout_gain", "ood_gain"))
    checks = {"harnessworld: RRSI harness transfers positively to the weak policy": hw["full"]["weak_unseen_gain"]["lo"] > 0,
              "harnessworld: unregularized transfers less": hw_pd["weak_unseen_gain"]["lo"] > 0,
              "agentqa: RRSI harness transfers positively to SimModel(skill=0.6)": aq["full"]["weak_unseen_gain"]["mean"] > 0}
    out = {"experiment": "E12 weak-policy transfer", "config": {"T": T, "seeds": a.seeds, "weak_policy": "WEAK "
                                                                "(strength -1.0; subagent -0.5, skill 0.5, client_tool 1.3)",
                                                                "agentqa_weak": "SimModel(skill=0.6)", "llm": a.llm},
           "harnessworld": {"summary": hw, "paired_full_minus_unregularized": hw_pd},
           "agentqa": {"summary": aq, "rows": aq_rows}, "checks": checks,
           "verdict": "REPRODUCED" if all(checks.values()) else
           "PARTIAL - not met: " + "; ".join(k for k, v in checks.items() if not v),
           "rows": strip_curves(rows)}
    save("e12_weak_policy", out)
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(1, 2, figsize=(11, 4))
        for i, (name, s) in enumerate((("HarnessWorld (weak policy)", hw), ("AgentQA (SimModel skill 0.6)", aq))):
            for j, arm in enumerate(arms):
                ax[i].bar([x + 0.4 * j for x in range(2)], [s[arm][m]["mean"] * 100 for m in ("weak_holdout_gain", "weak_ood_gain")],
                          width=0.4, label=arm)
            ax[i].set_xticks([0.2, 1.2])
            ax[i].set_xticklabels(["held-out", "OOD"])
            ax[i].set_ylabel("gain over H_0 on the weak policy (points)")
            ax[i].set_title(name)
            ax[i].legend()
        fig.tight_layout()
        fig.savefig(RESULTS / "e12_weak_policy.png", dpi=110)
    except Exception as e:  # noqa: BLE001
        print("figure skipped:", e)
    print({k: {m: round(v[m]["mean"], 4) for m in metrics} for k, v in hw.items()})
    print({k: {m: round(v[m]["mean"], 4) for m in v} for k, v in aq.items()})
    print(out["verdict"])


if __name__ == "__main__":
    main()
