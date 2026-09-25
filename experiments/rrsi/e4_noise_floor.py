"""E4 - The noise floor prevents noise chasing and downhill walks.

(a) Null candidates. The proposer only ships mechanisms with exactly zero effect and zero
    cost. We record how often a null candidate's measured gain exceeds the calibrated band
    (a "gain above noise" false accept), next to the nominal rate implied by the TRUE null
    sd (analytic) at the calibrated delta, for delta from the within-task bootstrap (code
    default) and from R = 5 repeated base evaluations; and how often nulls are admitted
    under the default within-band rule and under the coding preset (w_s = 0).
(b) Downhill walk. The proposer only ships "cheaper but slightly worse" mechanisms
    (about -0.5 pt each, -10% tokens), which the within-band shaped rule likes. Arms:
    floor at S* - delta (paper), floor at S_t - delta, no floor, and keep-if-better. We
    track the true evolve score of the incumbent over 40 rounds.

Confirming outcome (spec E4): with S* - delta the true score stays within about delta of
its best; the S_t-relative or no floor slides down; the null false-accept rate is near
the calibrated level (z = 2 -> about 2.5% one-sided).
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np  # noqa: E402
from _common import RESULTS, mean_curves, parse_args, pmap, run_hw, save, strip_curves, summarize  # noqa: E402

from rsi.core import summarize_runs  # noqa: E402
from rsi.domains.harnessworld import make_domain  # noqa: E402
from rsi.rrsi import RegularizerSwitches  # noqa: E402

NULL_WORLD = {"null_effect_sd": 0.0, "null_cost": (0.0, 0.0), "n_null": 120}
NULL_SHARES = {"null": 1.0}
DRIFT_WORLD = {"harmful_effect": (-0.06, 0.01), "harmful_cost": (-0.12, -0.08), "n_harmful": 120}
DRIFT_SHARES = {"harmful": 1.0}


def true_null_sd(seed: int, k: int = 2) -> float:
    dom = make_domain(seed=seed, **NULL_WORLD)
    p_map = dom._state(dom.seed_artifact())[0]
    ids = dom.tasks.splits["evolve"]
    n = dom.world.cfg.n_criteria
    var = sum(p_map[i] * (1 - p_map[i]) / (n * k) for i in ids) / len(ids) ** 2
    return math.sqrt(2 * var)


def main():
    a = parse_args("E4: noise floor", default_seeds=20)
    T_null = 8 if a.quick else 20
    T_drift = 10 if a.quick else 40
    jobs = []
    null_arms = {"null|bootstrap delta (default rule)": ({}, "full"),
                 "null|repeat R=5 delta (default rule)": ({"calibration_repeats": 5}, "full"),
                 "null|bootstrap delta, coding within-band (w_s=0)": ({"w_s": 0.0}, "full")}
    for lab, (cfg, arm) in null_arms.items():
        jobs += [{"seed": s, "arm": arm, "label": lab, "cfg": {"T": T_null, **cfg}, "world": NULL_WORLD,
                  "proposer": {"shares": NULL_SHARES, "prune_p": 0.0}, "llm": a.llm} for s in range(a.seeds)]
    drift_arms = {"drift|floor S* - delta (paper)": RegularizerSwitches.full(),
                  "drift|floor S_t - delta": RegularizerSwitches.full().but(floor="S_t"),
                  "drift|no floor": RegularizerSwitches.full().but(floor="none"),
                  "drift|keep-if-better": RegularizerSwitches.full().but(selection="greedy")}
    for lab, sw in drift_arms.items():
        jobs += [{"seed": s, "arm": sw, "label": lab, "cfg": {"T": T_drift}, "world": DRIFT_WORLD,
                  "proposer": {"shares": DRIFT_SHARES, "prune_p": 0.0}, "llm": a.llm} for s in range(a.seeds)]
    rows = pmap(run_hw, jobs, a.workers if a.llm == "sim" else 1)
    # (a) null false-accept
    from scipy.stats import norm
    null_res = {}
    for lab in null_arms:
        rs = [r for r in rows if r["label"] == lab]
        frac = [r["null_false_gain"] / r["null_candidates"] for r in rs if r["null_candidates"]]
        adm = [(r["n_accepted"]) / max(1, r["null_candidates"]) for r in rs]
        nominal = [1 - norm.cdf(r["delta"] / true_null_sd(r["seed"])) for r in rs]
        ratio = [r["delta"] / true_null_sd(r["seed"]) for r in rs]
        null_res[lab] = {"false_gain_rate": summarize_runs(frac), "nominal_rate_at_calibrated_delta": summarize_runs(nominal),
                         "delta_over_true_sd": summarize_runs(ratio), "accepted_per_candidate": summarize_runs(adm),
                         "true_evolve_change": summarize_runs([r["evolve_gain"] for r in rs]),
                         "n_candidates": int(sum(r["null_candidates"] for r in rs))}
    # (b) drift
    drift = {}
    for lab in drift_arms:
        rs = [r for r in rows if r["label"] == lab]
        drops = [r["curve"]["evolve"][-1] - r["curve"]["evolve"][0] for r in rs]
        below_best = [r["curve"]["evolve"][-1] - max(r["curve"]["evolve"]) for r in rs]
        drift[lab] = {"true_change_vs_H0": summarize_runs(drops), "true_final_minus_best": summarize_runs(below_best),
                      "delta": summarize_runs([r["delta"] for r in rs]),
                      "token_ratio": summarize_runs([r["token_ratio"] for r in rs])}
    paper = drift["drift|floor S* - delta (paper)"]
    checks = {
        "S* floor keeps true score within ~2 delta of H_0": paper["true_change_vs_H0"]["mean"] > -2 * paper["delta"]["mean"],
        "S_t floor slides further": drift["drift|floor S_t - delta"]["true_change_vs_H0"]["mean"] < paper["true_change_vs_H0"]["mean"],
        "no floor slides further": drift["drift|no floor"]["true_change_vs_H0"]["mean"] < paper["true_change_vs_H0"]["mean"],
        "null false-accept ~2.5% with repeat-calibrated delta":
            null_res["null|repeat R=5 delta (default rule)"]["false_gain_rate"]["mean"] < 0.06,
    }
    out = {"experiment": "E4 noise floor", "config": {"seeds": a.seeds, "T_null": T_null, "T_drift": T_drift,
                                                      "null_world": NULL_WORLD, "drift_world": DRIFT_WORLD, "llm": a.llm},
           "null_candidates": null_res, "drift": drift, "checks": checks,
           "verdict": "REPRODUCED" if all(checks.values()) else
           "PARTIAL - not met: " + "; ".join(k for k, v in checks.items() if not v),
           "curves": {k: v for k, v in mean_curves(rows).items() if k.startswith("drift")},
           "rows": strip_curves(rows)}
    save("e4_noise_floor", out)
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(1, 2, figsize=(13, 4.2))
        for lab, c in out["curves"].items():
            ax[0].plot([x - c["evolve"][0] for x in c["evolve"]], label=lab.split("|")[1])
        ax[0].axhline(-paper["delta"]["mean"], color="k", ls=":", label="-delta")
        ax[0].set_xlabel("round")
        ax[0].set_ylabel("true evolve score change vs H_0")
        ax[0].set_title("Downhill walk through within-band losses")
        ax[0].legend(fontsize=8)
        labs = list(null_res)
        ax[1].bar(range(len(labs)), [null_res[l]["false_gain_rate"]["mean"] * 100 for l in labs], label="observed")
        ax[1].plot(range(len(labs)), [null_res[l]["nominal_rate_at_calibrated_delta"]["mean"] * 100 for l in labs],
                   "ko", label="nominal at calibrated delta")
        ax[1].axhline(2.3, color="r", ls="--", label="z = 2 target (2.3%)")
        ax[1].set_xticks(range(len(labs)))
        ax[1].set_xticklabels([l.split("|")[1] for l in labs], fontsize=7, rotation=10)
        ax[1].set_ylabel("% null candidates with dS > delta")
        ax[1].legend(fontsize=8)
        ax[1].set_title("Null false-accept rate")
        fig.tight_layout()
        fig.savefig(RESULTS / "e4_noise_floor.png", dpi=110)
    except Exception as e:  # noqa: BLE001
        print("figure skipped:", e)
    for k, v in null_res.items():
        print(k, {m: round(x["mean"], 4) for m, x in v.items() if isinstance(x, dict)})
    for k, v in drift.items():
        print(k, {m: round(x["mean"], 4) for m, x in v.items()})
    print(out["verdict"])


if __name__ == "__main__":
    main()
