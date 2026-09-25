"""E7 - The annealed edit budget improves attribution.

HarnessWorld, full RRSI with the budget schedule varied: cosine anneal b_max = 4 -> b_min = 1
(paper), constant b_max = 4, constant 1. Measures the hitchhiker rate (accepted bundles that
contain at least one harmful mechanism), the rank correlation between the recorded dS an
edit carries in the history and the edit's TRUE marginal effect on the incumbent (credit
quality), early-round progress (true evolve gain after 5 rounds), and final transfer.

Confirming outcome (spec E7): anneal has a lower hitchhiker rate than constant b_max and
faster early progress than constant 1.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import RESULTS, paired, parse_args, pmap, run_hw, save, strip_curves, summarize  # noqa: E402

from rsi.rrsi import RegularizerSwitches  # noqa: E402

ARMS = {"anneal 4->1 (paper)": RegularizerSwitches.full(),
        "constant b=4": RegularizerSwitches.full().but(budget_anneal=False, constant_budget=4, name="const4"),
        "constant b=1": RegularizerSwitches.full().but(budget_anneal=False, constant_budget=1, name="const1")}


def main():
    a = parse_args("E7: annealed vs constant edit budget", default_seeds=50)
    T = 8 if a.quick else 20
    early = 3 if a.quick else 5
    jobs = [{"seed": s, "arm": sw, "label": lab, "cfg": {"T": T}, "llm": a.llm}
            for s in range(a.seeds) for lab, sw in ARMS.items()]
    rows = pmap(run_hw, jobs, a.workers if a.llm == "sim" else 1)
    for r in rows:
        r["early_gain"] = r["curve"]["evolve"][early] - r["curve"]["evolve"][0]
        r["early_unseen_gain"] = 0.5 * (r["curve"]["holdout"][early] + r["curve"]["ood"][early]
                                        - r["curve"]["holdout"][0] - r["curve"]["ood"][0])
        if isinstance(r.get("credit_corr"), float) and math.isnan(r["credit_corr"]):
            r["credit_corr"] = None
    metrics = ("hitchhiker_rate", "credit_corr", "early_gain", "early_unseen_gain", "evolve_gain", "ood_gain",
               "unseen_gain", "token_ratio", "n_accepted")
    summ = summarize(rows, metrics=metrics)
    A, C4, C1 = list(ARMS)
    pd = {"anneal_minus_const4": {m: paired(rows, C4, A, m) for m in ("hitchhiker_rate", "early_gain", "ood_gain",
                                                                      "unseen_gain")},
          "anneal_minus_const1": {m: paired(rows, C1, A, m) for m in ("hitchhiker_rate", "early_gain", "ood_gain",
                                                                      "unseen_gain")}}
    checks = {"anneal: lower hitchhiker rate than constant b_max": pd["anneal_minus_const4"]["hitchhiker_rate"]["hi"] < 0,
              "anneal: faster early progress than constant 1": pd["anneal_minus_const1"]["early_gain"]["lo"] > 0}
    out = {"experiment": "E7 annealed budget attribution", "config": {"T": T, "seeds": a.seeds, "early_round": early,
                                                                      "llm": a.llm},
           "summary": summ, "paired": pd, "checks": checks,
           "verdict": "REPRODUCED" if all(checks.values()) else
           "PARTIAL - not met: " + "; ".join(k for k, v in checks.items() if not v),
           "rows": strip_curves(rows)}
    save("e7_budget", out)
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(1, 3, figsize=(15, 4))
        labs = list(ARMS)
        for i, (m, t) in enumerate((("hitchhiker_rate", "hitchhiker rate (accepted bundles)"),
                                    ("credit_corr", "Spearman(recorded dS, true marginal)"),
                                    ("early_gain", f"true evolve gain after {early} rounds"))):
            ax[i].bar(range(3), [summ[l][m]["mean"] for l in labs],
                      yerr=[[summ[l][m]["mean"] - summ[l][m]["lo"] for l in labs],
                            [summ[l][m]["hi"] - summ[l][m]["mean"] for l in labs]])
            ax[i].set_xticks(range(3))
            ax[i].set_xticklabels(labs, fontsize=8)
            ax[i].set_title(t)
        fig.tight_layout()
        fig.savefig(RESULTS / "e7_budget.png", dpi=110)
    except Exception as e:  # noqa: BLE001
        print("figure skipped:", e)
    for k, v in summ.items():
        print(k, {m: round(v[m]["mean"], 3) for m in metrics})
    print(out["verdict"])


if __name__ == "__main__":
    main()
