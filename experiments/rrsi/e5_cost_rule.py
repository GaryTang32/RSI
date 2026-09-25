"""E5 - The cost rule makes extra tokens earn their keep (lighter harness, paper Fig. 4).

HarnessWorld, full RRSI with the complexity-aware acceptance varied: beta1 swept (the
"extra relative cost each point of gain may buy"; paper default 40), the above-band cost
rule switched off, and the whole complexity term off (cost rule off AND w_c = 0 in the
within-band rule). Unregularized evolution is the reference in the OOD-vs-tokens plane.

Confirming outcome (spec E5): cost-rule-on gives fewer tokens and equal or higher OOD; the
unregularized arm sits in the "more tokens, lower OOD" region.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import RESULTS, paired, parse_args, pmap, run_hw, save, strip_curves, summarize  # noqa: E402

from rsi.rrsi import RegularizerSwitches  # noqa: E402

BETA1 = (0.0, 5.0, 10.0, 20.0, 40.0, 80.0, 160.0)


def main():
    a = parse_args("E5: cost rule and beta1 sweep", default_seeds=50)   # 20 seeds: borderline checks flipped between realizations
    T = 8 if a.quick else 20
    arms = {f"beta1={b:g}": ("full", {"beta1": b}) for b in BETA1}
    arms["cost rule off (above band)"] = (RegularizerSwitches.full().but(cost_rule=False, name="no_cost_rule"), {})
    arms["no complexity term (cost rule off, w_c=0)"] = (RegularizerSwitches.full().but(cost_rule=False, name="no_cost"),
                                                         {"w_c": 0.0})
    arms["unregularized"] = ("unregularized", {})
    jobs = [{"seed": s, "arm": sw, "label": lab, "cfg": {"T": T, **cfg}, "llm": a.llm}
            for s in range(a.seeds) for lab, (sw, cfg) in arms.items()]
    rows = pmap(run_hw, jobs, a.workers if a.llm == "sim" else 1)
    metrics = ("token_ratio", "ood_gain", "unseen_gain", "evolve_gain", "measured_gain", "n_mechanisms")
    summ = summarize(rows, metrics=metrics)
    on, off = "beta1=40", "no complexity term (cost rule off, w_c=0)"
    pd = {m: paired(rows, off, on, m) for m in ("token_ratio", "ood_gain", "unseen_gain", "evolve_gain")}
    checks = {"cost rule on -> fewer tokens": pd["token_ratio"]["hi"] < 0,
              "cost rule on -> OOD equal or higher": pd["ood_gain"]["lo"] > -0.01,
              "unregularized: more tokens and lower OOD than full": (
                  summ["unregularized"]["token_ratio"]["mean"] > summ[on]["token_ratio"]["mean"]
                  and summ["unregularized"]["ood_gain"]["mean"] < summ[on]["ood_gain"]["mean"])}
    out = {"experiment": "E5 cost rule", "config": {"T": T, "seeds": a.seeds, "beta1": BETA1, "llm": a.llm},
           "summary": summ, "paired_on_minus_off": pd, "checks": checks,
           "verdict": "REPRODUCED" if all(checks.values()) else
           "PARTIAL - not met: " + "; ".join(k for k, v in checks.items() if not v),
           "rows": strip_curves(rows)}
    save("e5_cost_rule", out)
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(1, 2, figsize=(13, 4.2))
        for lab, s in summ.items():
            mk = "s" if lab.startswith("beta1") else ("X" if lab == "unregularized" else "D")
            ax[0].plot(s["token_ratio"]["mean"], s["ood_gain"]["mean"] * 100, mk, ms=8, label=lab)
        ax[0].set_xscale("log")
        ax[0].set_xlabel("tokens per trial / H_0")
        ax[0].set_ylabel("true OOD gain (points)")
        ax[0].legend(fontsize=7)
        ax[0].set_title("OOD vs tokens")
        xs = [b for b in BETA1]
        ax[1].plot(range(len(xs)), [summ[f"beta1={b:g}"]["token_ratio"]["mean"] for b in xs], "o-", label="tokens x H_0")
        ax2 = ax[1].twinx()
        ax2.plot(range(len(xs)), [summ[f"beta1={b:g}"]["ood_gain"]["mean"] * 100 for b in xs], "s--", color="C1",
                 label="OOD gain (pts)")
        ax[1].set_xticks(range(len(xs)))
        ax[1].set_xticklabels([f"{b:g}" for b in xs])
        ax[1].set_xlabel("beta1")
        ax[1].set_ylabel("tokens x H_0")
        ax2.set_ylabel("OOD gain (pts)")
        ax[1].set_title("beta1 sweep")
        fig.tight_layout()
        fig.savefig(RESULTS / "e5_cost_rule.png", dpi=110)
    except Exception as e:  # noqa: BLE001
        print("figure skipped:", e)
    for k, v in summ.items():
        print(k, {m: round(v[m]["mean"], 3) for m in metrics})
    print(out["verdict"])


if __name__ == "__main__":
    main()
