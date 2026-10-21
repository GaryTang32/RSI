"""E7: the annealed edit budget improves attribution (hitchhikers, credit quality, early progress) vs constant budgets.

Run: python experiments/rrsi/e7_budget.py [--quick] [--seeds N]
"""
import math

from _common import figure, pairs, parse_args, pmap, print_means, run_hw, save, strip_curves, summarize, verdict

from rsi.rrsi import RegularizerSwitches

# with the released code's ceil and t <= T-1 the cosine anneal runs 4 x 8, 3 x 5, 2 x 7 and never reaches 1
ARMS = {"anneal 4->2 (paper code, ceil)": RegularizerSwitches.full(),
        "constant b=4": RegularizerSwitches.full().but(budget_anneal=False, constant_budget=4, name="const4"),
        "constant b=1": RegularizerSwitches.full().but(budget_anneal=False, constant_budget=1, name="const1")}


def main():
    a = parse_args("E7: annealed vs constant edit budget", default_seeds=50)
    T = 8 if a.quick else 20
    early = 3 if a.quick else 5
    jobs = [{"seed": s, "arm": sw, "label": lab, "cfg": {"T": T}, "llm": a.llm}
            for s in range(a.seeds) for lab, sw in ARMS.items()]
    rows = pmap(run_hw, jobs, a.procs)
    for r in rows:
        c = r["curve"]
        r["early_gain"] = c["evolve"][early] - c["evolve"][0]
        r["early_unseen_gain"] = 0.5 * (c["holdout"][early] + c["ood"][early] - c["holdout"][0] - c["ood"][0])
        if isinstance(r.get("credit_corr"), float) and math.isnan(r["credit_corr"]):
            r["credit_corr"] = None
    metrics = ("hitchhiker_rate", "credit_corr", "early_gain", "early_unseen_gain", "evolve_gain", "ood_gain",
               "unseen_gain", "token_ratio", "n_accepted")
    summ = summarize(rows, metrics=metrics)
    A, C4, C1 = list(ARMS)
    pm = ("hitchhiker_rate", "early_gain", "ood_gain", "unseen_gain")
    pd = {"anneal_minus_const4": pairs(rows, C4, A, pm), "anneal_minus_const1": pairs(rows, C1, A, pm)}
    checks = {"anneal: lower hitchhiker rate than constant b_max":
                  pd["anneal_minus_const4"]["hitchhiker_rate"]["hi"] < 0,
              "anneal: faster early progress than constant 1": pd["anneal_minus_const1"]["early_gain"]["lo"] > 0}
    out = {"experiment": "E7 annealed budget attribution", "config": {"T": T, "seeds": a.seeds, "early_round": early,
                                                                      "llm": a.llm},
           "summary": summ, "paired": pd, "checks": checks, "verdict": verdict(checks), "rows": strip_curves(rows)}
    save("e7_budget", out)

    def draw(ax):
        labs = list(ARMS)
        for i, (m, t) in enumerate((("hitchhiker_rate", "hitchhiker rate (accepted bundles)"),
                                    ("credit_corr", "Spearman(recorded dS, true marginal)"),
                                    ("early_gain", f"true evolve gain after {early} rounds"))):
            ss = [summ[lab][m] for lab in labs]
            ax[i].bar(range(3), [s["mean"] for s in ss], yerr=[[s["mean"] - s["lo"] for s in ss],
                                                              [s["hi"] - s["mean"] for s in ss]])
            ax[i].set_xticks(range(3))
            ax[i].set_xticklabels(labs, fontsize=8)
            ax[i].set_title(t)
    figure("e7_budget", draw, 3, figsize=(15, 4))
    print_means(summ, metrics)
    print(out["verdict"])


if __name__ == "__main__":
    main()
