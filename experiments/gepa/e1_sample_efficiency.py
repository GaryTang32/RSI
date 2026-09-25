"""E1 - Reflective evolution vs scalar-reward RL: rollouts to reach a target test score.

Claim [paper Obs.1][doc]: GEPA "reaches optimal test performance with 4-35x fewer
rollouts" than GRPO; spec E1 asks for >= 10x fewer rollouts to a target in Tier 1 and a
curve that dominates at every budget.

Tier 1 (RuleWorld, rich feedback, analytic test score): GEPA (B = 6000) and
ScoreOnlyReflection (B = 6000) vs ScalarRLBaseline (GRPO-style Bernoulli prompt policy,
group 12, 4 instances/step, B = 24000, lr in {0.5, 2, 8}; the reported RL arm uses the lr
with the best mean *validation* score). Target = 80% / 90% of the oracle test score.
Also reported (``matched_budget_ratio``): the paper's own comparison, GEPA at its budget
against RL at a 4x larger one (paired final test, seeds where RL / GEPA reach the oracle,
and the rollouts GEPA needs to reach RL's final score).

Tier 2 analogue (AgentQA two-module harness, SimModel): GEPA vs ScoreOnly vs ScalarRL
(brainstormed vocabulary) at equal budget, measured holdout / OOD accuracy.

    python experiments/gepa/e1_sample_efficiency.py [--llm sim|claude:haiku] [--seeds N] [--quick]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import (RESULTS, fmt, paired, parse_args, plot_curves, pool_map, reflection_llm, save,  # noqa: E402
                     summarize)

import numpy as np  # noqa: E402

from rsi.domains.ruleworld import make_domain  # noqa: E402
from rsi.gepa import (Config, RLConfig, curve_at, gepa_curve, rollouts_to_target, run, run_scalar_rl,  # noqa: E402
                      run_score_only, trajectory_curve)

GRID = [100, 200, 400, 800, 1600, 3200, 6000, 12000, 24000]
LRS = [0.5, 2.0, 8.0]
ARGS = None


def job(spec):
    arm, seed, B = spec
    d = make_domain(seed=seed)
    truth = lambda a: d.expected(a, "test")
    oracle = d.expected(d.oracle_artifact(), "test")
    if arm in ("gepa", "score_only"):
        fn = run if arm == "gepa" else run_score_only
        res = fn(d, d.seed_artifact(), llm_propose=reflection_llm(ARGS.llm, d.world),
                 config=Config(max_metric_calls=B, seed=seed))
        curve = gepa_curve(res, truth)
        val = res.meta["best_val"]
        usage = res.usage.get("reflection", {})
    else:
        lr = float(arm.split("=")[1])
        res = run_scalar_rl(d, d.seed_artifact(), config=RLConfig(max_metric_calls=B, lr=lr, seed=seed))
        curve = trajectory_curve(res, truth)
        val = res.meta["best_val"]
        usage = {}
    return {"arm": arm, "seed": seed, "oracle": oracle, "curve": curve, "at": curve_at(curve, GRID),
            "final_test": curve[-1][1], "best_val": val, "rollouts": curve[-1][0],
            "to80": rollouts_to_target(curve, 0.8 * oracle), "to90": rollouts_to_target(curve, 0.9 * oracle),
            "reflection_calls": usage.get("calls", 0)}


def agentqa_job(spec):
    arm, seed, B = spec
    from rsi.domains.agentqa import AgentQADomain, SimModel, make_suite
    from rsi.gepa import report, two_module_harness
    suite = make_suite(n_evolve=15, n_val=15, n_holdout=20, n_ood_per_family=5, seed=seed)
    dom = AgentQADomain(suite)
    llm_r = reflection_llm(ARGS.llm, kind="agentqa")
    seed_art = two_module_harness()
    if arm == "scalar_rl":
        res = run_scalar_rl(dom, seed_art, llm_task=SimModel(suite), llm_propose=llm_r,
                            config=RLConfig(max_metric_calls=B, group_size=6, instances_per_step=2, val_every=3,
                                            lr=2.0, vocab_size=12, seed=seed))
    else:
        fn = run if arm == "gepa" else run_score_only
        res = fn(dom, seed_art, llm_task=SimModel(suite), llm_propose=llm_r,
                 config=Config(max_metric_calls=B, seed=seed, workers=4))
    rep = report(dom, SimModel(suite), {"seed": res.baseline, "best": res.best}, splits=("holdout", "ood"))
    return {"arm": arm, "seed": seed, "val": res.meta.get("best_val"),
            "holdout": rep["splits"]["holdout"]["best"]["S"], "ood": rep["splits"]["ood"]["best"]["S"],
            "holdout_seed": rep["splits"]["holdout"]["seed"]["S"], "ood_seed": rep["splits"]["ood"]["seed"]["S"],
            "best_files": {k: v for k, v in res.best.files.items() if k.startswith("prompts/")}}


def main():
    global ARGS
    ARGS = a = parse_args("E1: sample efficiency vs scalar-reward RL", default_seeds=20,
                          extra=lambda ap: ap.add_argument("--agentqa-seeds", type=int, default=None))
    B_g = 400 if a.live else (1600 if a.quick else 6000)
    B_rl = 4000 if a.quick else 24000
    seeds = list(range(a.seeds))
    jobs = [(arm, s, B_g) for s in seeds for arm in ("gepa", "score_only")]
    jobs += [(f"rl_lr={lr}", s, B_rl) for s in seeds for lr in LRS]
    rows = pool_map(job, jobs, a.workers)
    by = {}
    for r in rows:
        by.setdefault(r["arm"], []).append(r)
    rl_arms = [f"rl_lr={lr}" for lr in LRS]
    best_rl = max(rl_arms, key=lambda k: np.mean([r["best_val"] for r in by[k]]))
    oracle = float(np.mean([r["oracle"] for r in rows]))
    summary = {}
    for arm, rs in by.items():
        rs.sort(key=lambda r: r["seed"])
        summary[arm] = {
            "final_test": summarize([r["final_test"] for r in rs]),
            "best_val": summarize([r["best_val"] for r in rs]),
            "at_budget": {str(b): summarize([r["at"][j] for r in rs]) for j, b in enumerate(GRID)},
            "rollouts_to_80pct": summarize([r["to80"] for r in rs]),
            "reached_80pct": float(np.mean([r["to80"] is not None for r in rs])),
            "rollouts_to_90pct": summarize([r["to90"] for r in rs]),
            "reached_90pct": float(np.mean([r["to90"] is not None for r in rs])),
        }
    ratios = {}
    for tgt in ("to80", "to90"):
        g = {r["seed"]: r[tgt] for r in by["gepa"]}
        rr = {r["seed"]: r[tgt] for r in by[best_rl]}
        both = [(rr[s] / g[s]) for s in seeds if g.get(s) and rr.get(s)]
        cens = [(B_rl / g[s]) for s in seeds if g.get(s) and not rr.get(s)]
        ratios[tgt] = {"median_ratio_rl_over_gepa": float(np.median(both + cens)) if both + cens else None,
                       "ratio_summary_reached_both": summarize(both), "n_rl_censored": len(cens),
                       "n_gepa_not_reached": sum(1 for s in seeds if not g.get(s))}
    dominate = {str(b): summary["gepa"]["at_budget"][str(b)]["mean"] >= summary[best_rl]["at_budget"][str(b)]["mean"]
                for b in GRID if b <= B_g}
    gepa_vs_so = paired([r["final_test"] for r in by["score_only"]], [r["final_test"] for r in by["gepa"]])
    # ---- the paper's own comparison: GEPA at its budget vs RL at a 4x larger one (claim audit, finding 5)
    g_by = {r["seed"]: r for r in by["gepa"]}
    rl_by = {r["seed"]: r for r in by[best_rl]}
    both = [s for s in seeds if s in g_by and s in rl_by]
    matched_ratio = {
        "budgets": {"gepa": B_g, "rl": B_rl, "ratio_rl_over_gepa": B_rl / B_g},
        "gepa_minus_rl_final": paired([rl_by[s]["final_test"] for s in both], [g_by[s]["final_test"] for s in both]),
        "gepa_wins_seeds": sum(g_by[s]["final_test"] > rl_by[s]["final_test"] + 1e-12 for s in both),
        "rl_wins_seeds": sum(rl_by[s]["final_test"] > g_by[s]["final_test"] + 1e-12 for s in both),
        "rl_reached_oracle_seeds": sum(rl_by[s]["final_test"] >= rl_by[s]["oracle"] - 1e-9 for s in both),
        "gepa_reached_oracle_seeds": sum(g_by[s]["final_test"] >= g_by[s]["oracle"] - 1e-9 for s in both),
        "paired_gepa_minus_rl_at_equal_budget": {
            str(b): paired([rl_by[s]["at"][j] for s in both], [g_by[s]["at"][j] for s in both])
            for j, b in enumerate(GRID) if b <= B_g},
    }
    # rollouts GEPA needs to reach RL's *final* (B_rl) test score, per seed (censored when it never does)
    x = {s: rollouts_to_target(g_by[s]["curve"], rl_by[s]["final_test"]) for s in both}
    got = [s for s in both if x[s]]
    matched_ratio["gepa_rollouts_to_rl_final"] = {
        "n_matched": len(got), "n": len(both), "rollouts": summarize([x[s] for s in got]),
        "ratio_rl_budget_over_gepa_rollouts": {
            "median": float(np.median([B_rl / x[s] for s in got])) if got else None,
            "min": float(min(B_rl / x[s] for s in got)) if got else None,
            "max": float(max(B_rl / x[s] for s in got)) if got else None}}
    out = {"experiment": "E1 sample efficiency vs scalar-reward RL", "llm": a.llm, "seeds": seeds,
           "config": {"budget_gepa": B_g, "budget_rl": B_rl, "rl_lrs": LRS, "rl_group": 12, "rl_instances": 4,
                      "world": "RuleWorld default (2 modules, 16 aspects, 2 conflicts, partial scoring, rich mu_f)",
                      "oracle_test": oracle, "grid": GRID},
           "selected_rl_arm": best_rl, "summary": summary, "rollout_ratio": ratios,
           "gepa_dominates_rl_at_budgets": dominate, "gepa_minus_score_only_final": gepa_vs_so,
           "matched_budget_ratio": matched_ratio,
           "raw": [{k: v for k, v in r.items() if k != "curve"} for r in rows]}
    r90 = ratios["to90"]["median_ratio_rl_over_gepa"]
    r80 = ratios["to80"]["median_ratio_rl_over_gepa"]
    out["verdict"] = (
        f"GEPA reaches 80%/90% of the oracle test score after mean "
        f"{summary['gepa']['rollouts_to_80pct']['mean']:.0f}/{summary['gepa']['rollouts_to_90pct']['mean']:.0f} "
        f"rollouts (mean; reached in {summary['gepa']['reached_80pct']:.0%}/{summary['gepa']['reached_90pct']:.0%} "
        f"of seeds) vs scalar RL ({best_rl}) "
        f"{summary[best_rl]['rollouts_to_80pct']['mean']:.0f}/{summary[best_rl]['rollouts_to_90pct']['mean']:.0f} "
        f"(mean over seeds that reached it; reached {summary[best_rl]['reached_80pct']:.0%}/{summary[best_rl]['reached_90pct']:.0%}); median per-seed "
        f"ratio RL/GEPA = {r80 if r80 is None else round(r80, 1)}x (80%) and "
        f"{r90 if r90 is None else round(r90, 1)}x (90%). Claim '>=10x fewer rollouts': "
        f"{'REPRODUCED' if (r80 or 0) >= 10 else 'NOT reproduced at 10x'} at the 80% target, "
        f"{'REPRODUCED' if (r90 or 0) >= 10 else 'NOT reproduced at 10x'} at the 90% target. GEPA curve >= RL curve at "
        f"{sum(dominate.values())}/{len(dominate)} budgets <= {B_g}. Final test at equal-or-larger RL budget: GEPA "
        f"{fmt(summary['gepa']['final_test'])} (B={B_g}) vs RL {fmt(summary[best_rl]['final_test'])} (B={B_rl}).")
    mm = matched_ratio["gepa_minus_rl_final"]
    sig = mm and (mm["lo"] > 0 or mm["hi"] < 0)
    gm = matched_ratio["gepa_rollouts_to_rl_final"]
    out["verdict_matched_ratio"] = (
        f"At the paper's 1:{B_rl / B_g:g} budget ratio (GEPA B={B_g} vs RL B={B_rl}, paired over {len(both)} seeds): "
        f"GEPA - RL final test = {mm['mean_diff']:+.3f} [{mm['lo']:+.3f}, {mm['hi']:+.3f}] "
        f"({'significant' if sig else 'NOT significant'}); GEPA higher in {matched_ratio['gepa_wins_seeds']}, "
        f"RL higher in {matched_ratio['rl_wins_seeds']} seeds; RL reaches the oracle test score in "
        f"{matched_ratio['rl_reached_oracle_seeds']}/{len(both)} seeds, GEPA in "
        f"{matched_ratio['gepa_reached_oracle_seeds']}/{len(both)}. GEPA reaches RL's final score in "
        f"{gm['n_matched']}/{gm['n']} seeds"
        + (f" ({gm['ratio_rl_budget_over_gepa_rollouts']['min']:.1f}-"
           f"{gm['ratio_rl_budget_over_gepa_rollouts']['max']:.1f}x fewer rollouts, median "
           f"{gm['ratio_rl_budget_over_gepa_rollouts']['median']:.1f}x)" if gm["n_matched"] else "")
        + f". So 'outperforms RL' is judged at matched budgets by the curves above; at the 1:{B_rl / B_g:g} ratio it is "
        + ("a win" if sig and mm["mean_diff"] > 0 else ("a loss" if sig else "a tie")) + " here.")
    # ---- AgentQA Tier-2 analogue
    n_aq = a.agentqa_seeds if a.agentqa_seeds is not None else (1 if a.live else (2 if a.quick else 5))
    if n_aq > 0:
        B_aq = 300
        aq_rows = pool_map(agentqa_job, [(arm, s, B_aq) for s in range(n_aq)
                                         for arm in ("gepa", "score_only", "scalar_rl")], a.workers)
        aq = {}
        for r in aq_rows:
            aq.setdefault(r["arm"], []).append(r)
        out["agentqa"] = {"budget": B_aq, "harness": "two-module (solver -> python tool -> reporter), SimModel",
                          "summary": {arm: {k: summarize([r[k] for r in rs]) for k in
                                            ("val", "holdout", "ood", "holdout_seed", "ood_seed")}
                                      for arm, rs in aq.items()},
                          "raw": aq_rows}
        s = out["agentqa"]["summary"]
        out["verdict_agentqa"] = (
            f"AgentQA (B={B_aq}, {n_aq} seeds): holdout/OOD accuracy GEPA {fmt(s['gepa']['holdout'])} / "
            f"{fmt(s['gepa']['ood'])}, ScoreOnly {fmt(s['score_only']['holdout'])} / {fmt(s['score_only']['ood'])}, "
            f"ScalarRL {fmt(s['scalar_rl']['holdout'])} / {fmt(s['scalar_rl']['ood'])} "
            f"(seed harness {fmt(s['gepa']['holdout_seed'])} / {fmt(s['gepa']['ood_seed'])}).")
    save("e1_sample_efficiency", out, a.out)
    curves = {"GEPA": [r["at"] for r in by["gepa"]], "ScoreOnlyReflection": [r["at"] for r in by["score_only"]]}
    for arm in rl_arms:
        curves[f"ScalarRL {arm.split('_')[1]}" + (" (selected)" if arm == best_rl else "")] = [r["at"] for r in by[arm]]
    plot_curves(RESULTS / "e1_sample_efficiency.png", GRID, curves,
                "E1: RuleWorld true test score of returned prompt vs rollouts", hline=0.9 * oracle)
    print(out["verdict"])
    print(out["verdict_matched_ratio"])
    if "verdict_agentqa" in out:
        print(out["verdict_agentqa"])


if __name__ == "__main__":
    main()
