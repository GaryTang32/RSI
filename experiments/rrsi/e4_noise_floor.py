"""E4: the noise floor stops noise chasing (null-only proposer) and downhill walks (cheaper-but-worse proposer).

Run: python experiments/rrsi/e4_noise_floor.py [--quick] [--seeds N]
"""
import math

from _common import figure, mean_curves, parse_args, pmap, run_hw, save, strip_curves, verdict
from scipy.stats import norm

from rsi.core import summarize_runs
from rsi.domains.harnessworld import make_domain
from rsi.rrsi import RegularizerSwitches

NULL_WORLD = {"null_effect_sd": 0.0, "null_cost": (0.0, 0.0), "n_null": 120}
NULL_SHARES = {"null": 1.0}
DRIFT_WORLD = {"harmful_effect": (-0.06, 0.01), "harmful_cost": (-0.12, -0.08), "n_harmful": 120}
DRIFT_SHARES = {"harmful": 1.0}


def true_null_sd(seed: int, k: int = 2) -> float:
    """Analytic sd of a null candidate's dS on the NULL_WORLD evolve split."""
    dom = make_domain(seed=seed, **NULL_WORLD)
    p_map = dom._state(dom.seed_artifact())[0]
    ids = dom.tasks.splits["evolve"]
    n = dom.world.cfg.n_criteria
    var = sum(p_map[i] * (1 - p_map[i]) / (n * k) for i in ids) / len(ids) ** 2
    return math.sqrt(2 * var)


def null_rates(rs: list) -> dict:
    ratio = [r["delta"] / true_null_sd(r["seed"]) for r in rs]
    n0 = int(sum(r.get("null_candidates_r0", 0) for r in rs))
    return {"false_gain_rate": summarize_runs([r["null_false_gain"] / r["null_candidates"] for r in rs
                                               if r["null_candidates"]]),
            "nominal_rate_at_calibrated_delta": summarize_runs([1 - norm.cdf(x) for x in ratio]),
            # round 0 compares against H_0's own (unselected) measurement: no winner's curse yet
            "false_gain_rate_round0": {**summarize_runs([r["null_false_gain_r0"] / r["null_candidates_r0"] for r in rs
                                                         if r.get("null_candidates_r0")]),
                                       "n_candidates": n0,
                                       "pooled": (sum(r["null_false_gain_r0"] for r in rs) / n0) if n0 else None},
            "delta_over_true_sd": summarize_runs(ratio),
            "accepted_per_candidate": summarize_runs([r["n_accepted"] / max(1, r["null_candidates"]) for r in rs]),
            "true_evolve_change": summarize_runs([r["evolve_gain"] for r in rs]),
            "n_candidates": int(sum(r["null_candidates"] for r in rs))}


def drift_stats(rs: list) -> dict:
    ev = [r["curve"]["evolve"] for r in rs]
    return {"true_change_vs_H0": summarize_runs([c[-1] - c[0] for c in ev]),
            "true_final_minus_best": summarize_runs([c[-1] - max(c) for c in ev]),
            "delta": summarize_runs([r["delta"] for r in rs]),
            "token_ratio": summarize_runs([r["token_ratio"] for r in rs])}


def main():
    a = parse_args("E4: noise floor", default_seeds=20)
    T_null = 8 if a.quick else 20
    T_drift = 10 if a.quick else 40
    null_arms = {"null|bootstrap delta (default rule)": {},
                 "null|repeat R=5 delta (default rule)": {"calibration_repeats": 5},
                 "null|bootstrap delta, coding within-band (w_s=0)": {"w_s": 0.0}}
    drift_arms = {"drift|floor S* - delta (paper)": RegularizerSwitches.full(),
                  "drift|floor S_t - delta": RegularizerSwitches.full().but(floor="S_t"),
                  "drift|no floor": RegularizerSwitches.full().but(floor="none"),
                  "drift|keep-if-better": RegularizerSwitches.full().but(selection="greedy")}
    jobs = [{"seed": s, "arm": "full", "label": lab, "cfg": {"T": T_null, **cfg}, "world": NULL_WORLD,
             "proposer": {"shares": NULL_SHARES, "prune_p": 0.0}, "llm": a.llm}
            for lab, cfg in null_arms.items() for s in range(a.seeds)]
    jobs += [{"seed": s, "arm": sw, "label": lab, "cfg": {"T": T_drift}, "world": DRIFT_WORLD,
              "proposer": {"shares": DRIFT_SHARES, "prune_p": 0.0}, "llm": a.llm}
             for lab, sw in drift_arms.items() for s in range(a.seeds)]
    rows = pmap(run_hw, jobs, a.procs)
    null_res = {lab: null_rates([r for r in rows if r["label"] == lab]) for lab in null_arms}
    drift = {lab: drift_stats([r for r in rows if r["label"] == lab]) for lab in drift_arms}
    paper = drift["drift|floor S* - delta (paper)"]
    change = {lab: d["true_change_vs_H0"]["mean"] for lab, d in drift.items()}
    paper_change = change["drift|floor S* - delta (paper)"]
    checks = {
        "S* floor keeps true score within ~2 delta of H_0": paper_change > -2 * paper["delta"]["mean"],
        "S_t floor slides further": change["drift|floor S_t - delta"] < paper_change,
        "no floor slides further": change["drift|no floor"] < paper_change,
        # not a calibration test (E10): later rounds compare against a winner's-curse-inflated incumbent
        "null false-gain rate over the run at or below the z=2 level (2.3%), repeat-calibrated delta":
            null_res["null|repeat R=5 delta (default rule)"]["false_gain_rate"]["mean"] <= 0.023,
        "null false-gain rate over the run at or below the z=2 level (2.3%), bootstrap delta":
            null_res["null|bootstrap delta (default rule)"]["false_gain_rate"]["mean"] <= 0.023,
    }
    notes = {"null_rates": "false_gain_rate pools every round; after the first acceptance the incumbent's stored score "
                           "is the max of noisy measurements (winner's curse), so a null candidate rarely beats it by "
                           "delta. false_gain_rate_round0 compares against H_0's unselected measurement and should "
                           "be close to nominal_rate_at_calibrated_delta, which is ~8% (not 2.3%) because the "
                           "within-task bootstrap underestimates the null sd at k = 2 (see E10)."}
    out = {"experiment": "E4 noise floor",
           "config": {"seeds": a.seeds, "T_null": T_null, "T_drift": T_drift, "null_world": NULL_WORLD,
                      "drift_world": DRIFT_WORLD, "llm": a.llm},
           "null_candidates": null_res, "drift": drift, "checks": checks, "notes": notes, "verdict": verdict(checks),
           "curves": {k: v for k, v in mean_curves(rows).items() if k.startswith("drift")},
           "rows": strip_curves(rows)}
    save("e4_noise_floor", out)

    def draw(ax):
        for lab, c in out["curves"].items():
            ax[0].plot([x - c["evolve"][0] for x in c["evolve"]], label=lab.split("|")[1])
        ax[0].axhline(-paper["delta"]["mean"], color="k", ls=":", label="-delta")
        ax[0].set_xlabel("round")
        ax[0].set_ylabel("true evolve score change vs H_0")
        ax[0].set_title("Downhill walk through within-band losses")
        ax[0].legend(fontsize=8)
        labs = list(null_res)
        ax[1].bar(range(len(labs)), [null_res[lab]["false_gain_rate"]["mean"] * 100 for lab in labs], label="observed")
        ax[1].plot(range(len(labs)), [null_res[lab]["nominal_rate_at_calibrated_delta"]["mean"] * 100 for lab in labs],
                   "ko", label="nominal at calibrated delta")
        ax[1].axhline(2.3, color="r", ls="--", label="z = 2 target (2.3%)")
        ax[1].set_xticks(range(len(labs)))
        ax[1].set_xticklabels([lab.split("|")[1] for lab in labs], fontsize=7, rotation=10)
        ax[1].set_ylabel("% null candidates with dS > delta")
        ax[1].legend(fontsize=8)
        ax[1].set_title("Null false-accept rate")
    figure("e4_noise_floor", draw, 2, figsize=(13, 4.2))
    for k, v in null_res.items():
        print(k, {m: round(x["mean"], 4) for m, x in v.items() if isinstance(x, dict)})
    for k, v in drift.items():
        print(k, {m: round(x["mean"], 4) for m, x in v.items()})
    print(out["verdict"])


if __name__ == "__main__":
    main()
