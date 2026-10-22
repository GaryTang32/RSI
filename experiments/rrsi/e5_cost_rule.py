"""E5: the cost rule makes extra tokens earn their keep (paper Fig. 4): beta1 sweep and cost-rule ablations.

Run: python experiments/rrsi/e5_cost_rule.py [--quick] [--seeds N]
"""
from _common import figure, pairs, parse_args, pmap, print_means, run_hw, save, strip_curves, summarize, verdict

from rsi.rrsi import RegularizerSwitches

BETA1 = (0.0, 5.0, 10.0, 20.0, 40.0, 80.0, 160.0)


def main():
    a = parse_args("E5: cost rule and beta1 sweep", default_seeds=50)
    T = 8 if a.quick else 20
    arms = {f"beta1={b:g}": ("full", {"beta1": b}) for b in BETA1}
    arms["cost rule off (above band)"] = (RegularizerSwitches.full().but(cost_rule=False, name="no_cost_rule"), {})
    arms["no complexity term (cost rule off, w_c=0)"] = (
        RegularizerSwitches.full().but(cost_rule=False, name="no_cost"), {"w_c": 0.0})
    arms["unregularized"] = ("unregularized", {})
    jobs = [{"seed": s, "arm": sw, "label": lab, "cfg": {"T": T, **cfg}, "llm": a.llm}
            for s in range(a.seeds) for lab, (sw, cfg) in arms.items()]
    rows = pmap(run_hw, jobs, a.procs)
    metrics = ("token_ratio", "ood_gain", "unseen_gain", "evolve_gain", "measured_gain", "n_mechanisms")
    summ = summarize(rows, metrics=metrics)
    on, off = "beta1=40", "no complexity term (cost rule off, w_c=0)"
    pd = pairs(rows, off, on, ("token_ratio", "ood_gain", "unseen_gain", "evolve_gain"))
    unreg = summ["unregularized"]
    checks = {"cost rule on -> fewer tokens": pd["token_ratio"]["hi"] < 0,
              "cost rule on -> OOD equal or higher": pd["ood_gain"]["lo"] > -0.01,
              "unregularized: more tokens and lower OOD than full": (
                  unreg["token_ratio"]["mean"] > summ[on]["token_ratio"]["mean"]
                  and unreg["ood_gain"]["mean"] < summ[on]["ood_gain"]["mean"])}
    out = {"experiment": "E5 cost rule", "config": {"T": T, "seeds": a.seeds, "beta1": BETA1, "llm": a.llm},
           "summary": summ, "paired_on_minus_off": pd, "checks": checks, "verdict": verdict(checks),
           "rows": strip_curves(rows)}
    save("e5_cost_rule", out)

    def draw(ax):
        for lab, s in summ.items():
            mk = "s" if lab.startswith("beta1") else ("X" if lab == "unregularized" else "D")
            ax[0].plot(s["token_ratio"]["mean"], s["ood_gain"]["mean"] * 100, mk, ms=8, label=lab)
        ax[0].set_xscale("log")
        ax[0].set_xlabel("tokens per trial / H_0")
        ax[0].set_ylabel("true OOD gain (points)")
        ax[0].legend(fontsize=7)
        ax[0].set_title("OOD vs tokens")
        sweep = [summ[f"beta1={b:g}"] for b in BETA1]
        ax[1].plot(range(len(BETA1)), [s["token_ratio"]["mean"] for s in sweep], "o-", label="tokens x H_0")
        ax2 = ax[1].twinx()
        ax2.plot(range(len(BETA1)), [s["ood_gain"]["mean"] * 100 for s in sweep], "s--", color="C1",
                 label="OOD gain (pts)")
        ax[1].set_xticks(range(len(BETA1)))
        ax[1].set_xticklabels([f"{b:g}" for b in BETA1])
        ax[1].set_xlabel("beta1")
        ax[1].set_ylabel("tokens x H_0")
        ax2.set_ylabel("OOD gain (pts)")
        ax[1].set_title("beta1 sweep")
    figure("e5_cost_rule", draw, 2, figsize=(13, 4.2))
    print_means(summ, metrics)
    print(out["verdict"])


if __name__ == "__main__":
    main()
