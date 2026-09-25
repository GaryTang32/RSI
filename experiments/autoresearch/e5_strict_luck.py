"""E5 - "strictly better" on one noisy number locks in luck.

Claim [doc]: "Keeping anything 'strictly better' on one noisy number can lock in
lucky results" (upstream even kept "random seed 42 -> 137"; the MLX maintainers:
"the recorded curve is an optimistic running-minimum that regresses on honest re-eval").

Keep rules compared at EQUAL TOTAL TRAINING RUNS (``Config.max_runs``):
  strict        upstream: one run, keep iff strictly lower
  rigor         autoresearch-mlx rigor.py: 3 runs with fresh seeds, keep iff P_boot(better) >= 0.95,
                early discard of clear losers, never re-score an identical file
  rigor_pinned  rigor.py literally: 3 repeats of the pinned seed (only nondeterminism varies)
  noise_band    core MinGain gate: keep iff gain > delta (delta from 5 baseline re-runs)
  simplicity    SimplicityWeighted (reduces to strict for one-line knob edits)

A. Landscape (exact truth, 50 seeds): false-keep rate (keeps whose TRUE delta <= 0), seed-only
   keeps, optimism gap = recorded best - mean of 5 fresh-seed re-runs of the final commit,
   true final quality.
B. tinylm (real run-to-run noise, 3 seeds): strict vs rigor, optimism gap and honest gain.

Usage: python experiments/autoresearch/e5_strict_luck.py [--seeds N] [--quick] [--llm ...]
"""
from __future__ import annotations

from _common import SCRATCH, ci, parser, plt, pool_map, write  # noqa: I001

import json

import numpy as np

from rsi.autoresearch import (AutoresearchLoop, BootstrapRigorKeep, Config, GateKeep, LandscapeTask, MockResearchAgent,
                              Reeval, SimplicityWeighted, StrictKeep, landscape_edit_pool)
from rsi.core import MinGain

RULES = ("strict", "rigor", "rigor_pinned", "noise_band", "simplicity")


def make_rule(name: str):
    if name == "strict":
        return StrictKeep()
    if name == "rigor":
        return BootstrapRigorKeep()
    if name == "rigor_pinned":
        return BootstrapRigorKeep(vary_seed=False)
    if name == "noise_band":
        return GateKeep(MinGain(), name="noise_band")
    if name == "simplicity":
        return SimplicityWeighted(eps=0.001)
    raise ValueError(name)


def landscape_arm(args):
    rule, seed, max_runs = args
    task = LandscapeTask(seed=seed)
    ag = MockResearchAgent(landscape_edit_pool(), seed=seed)
    cfg = Config(max_experiments=None, max_runs=max_runs, persist=False, plot=False, hidden_audit=False, seed=seed,
                 noise_runs=5 if rule == "noise_band" else 0)
    loop = AutoresearchLoop(task, ag, cfg, keep_rule=make_rule(rule), out_dir=SCRATCH / "e5" / "ls")
    res = loop.run()
    keeps = [n for n in res.ledger.nodes() if n.status == "keep"]
    true_d = [keeps[i - 1].metrics["truth"] - keeps[i].metrics["truth"] for i in range(1, len(keeps))]
    seed_keeps = sum(1 for n in keeps if n.meta.get("edit") == "seed_change")
    honest = Reeval(task).run(res.best, [50_000 + i for i in range(5)])
    rec = float(np.mean(loop.inc["samples"].values))
    return {"rule": rule, "seed": seed, "n_keeps": len(keeps) - 1, "n_runs": loop.n_runs,
            "n_experiments": loop.n_experiments, "false_keep_rate": float(np.mean([d <= 0 for d in true_d]))
            if true_d else 0.0, "n_false_keeps": int(sum(d <= 0 for d in true_d)), "seed_keeps": seed_keeps,
            "recorded_best": rec, "honest_final": honest["mean"], "optimism_gap": honest["mean"] - rec,
            "true_final": task.truth(res.best), "true_gain": task.truth(res.baseline) - task.truth(res.best)}


def tinylm_arm(args):
    rule, seed, max_runs = args
    from rsi.domains.tinylm import TinyLMTask

    task = TinyLMTask(budget_s=2.0)
    ag = MockResearchAgent(task.mock_edit_pool(), seed=seed)
    loop = AutoresearchLoop(task, ag, Config(max_experiments=None, max_runs=max_runs, hidden_audit=False, seed=seed, overwrite=True,
                                             reeval_seeds=5, tag=f"e5-{rule}-{seed}"),
                            keep_rule=make_rule(rule), out_dir=SCRATCH / "e5" / f"tinylm_{rule}_{seed}")
    res = loop.run()
    keeps = [n for n in res.ledger.nodes() if n.status == "keep"]
    rv = res.meta["reeval"]
    return {"rule": rule, "seed": seed, "n_keeps": len(keeps) - 1, "n_runs": loop.n_runs,
            "n_experiments": loop.n_experiments, "seed_keeps": sum(1 for n in keeps if n.meta.get("edit") == "seed_change"),
            "recorded_best": rv["recorded_best"], "honest_final": rv["final"]["mean"],
            "honest_baseline": rv["baseline"]["mean"], "optimism_gap": rv["optimism_gap"],
            "honest_gain": rv["baseline"]["mean"] - rv["final"]["mean"], "kept": [n.change for n in keeps[1:]]}


def agg(rows, keys):
    return {k: ci([r[k] for r in rows]) for k in keys}


def main():
    ap = parser(__doc__.splitlines()[0], seeds=3)
    a = ap.parse_args()
    ls_seeds = list(range(8 if a.quick else 50))
    max_runs = 60 if a.quick else 150
    ls = pool_map(landscape_arm, [(r, s, max_runs) for r in RULES for s in ls_seeds], a.workers)
    out = {"config": {"landscape_seeds": len(ls_seeds), "max_runs": max_runs, "rules": RULES,
                      "noise": {"seed_sd": 0.002, "nondeterminism_sd": 0.001, "val_sd": 0.0015}}}
    keys = ["n_keeps", "false_keep_rate", "n_false_keeps", "seed_keeps", "optimism_gap", "true_final", "true_gain",
            "n_experiments"]
    out["landscape"] = {r: agg([x for x in ls if x["rule"] == r], keys) for r in RULES}
    t_seeds = list(range(1 if a.quick else a.seeds))
    t_runs = 10 if a.quick else 27
    tl = pool_map(tinylm_arm, [(r, s, t_runs) for r in ("strict", "rigor") for s in t_seeds], a.workers)
    out["tinylm"] = {r: {**agg([x for x in tl if x["rule"] == r], ["n_keeps", "seed_keeps", "optimism_gap",
                                                                    "honest_gain", "n_experiments"]),
                         "runs": [x for x in tl if x["rule"] == r]} for r in ("strict", "rigor")}
    L = out["landscape"]
    verdict = {
        "strict_false_keep_rate": L["strict"]["false_keep_rate"]["mean"],
        "rigor_false_keep_rate": L["rigor"]["false_keep_rate"]["mean"],
        "strict_seed_keeps_per_run": L["strict"]["seed_keeps"]["mean"],
        "rigor_seed_keeps_per_run": L["rigor"]["seed_keeps"]["mean"],
        "strict_optimism_gap": L["strict"]["optimism_gap"]["mean"],
        "rigor_optimism_gap": L["rigor"]["optimism_gap"]["mean"],
        "strict_optimism_gap_ci_lo": L["strict"]["optimism_gap"]["lo"],
        "true_final_strict": L["strict"]["true_final"]["mean"], "true_final_rigor": L["rigor"]["true_final"]["mean"],
        "tinylm_strict_optimism_gap": out["tinylm"]["strict"]["optimism_gap"]["mean"],
        "tinylm_rigor_optimism_gap": out["tinylm"]["rigor"]["optimism_gap"]["mean"],
    }
    verdict["strict_locks_in_luck"] = bool(L["strict"]["optimism_gap"]["lo"] > 0 and
                                           L["strict"]["false_keep_rate"]["mean"] > L["rigor"]["false_keep_rate"]["mean"])
    verdict["rigor_smaller_gap"] = bool(L["rigor"]["optimism_gap"]["mean"] < L["strict"]["optimism_gap"]["mean"])
    verdict["honest_final_quality_tradeoff"] = (
        "rigor better" if L["rigor"]["true_final"]["mean"] < L["strict"]["true_final"]["mean"] else
        "strict better at equal compute (rigor spends up to 3 runs per candidate: "
        f"{L['rigor']['n_experiments']['mean']:.0f} vs {L['strict']['n_experiments']['mean']:.0f} ideas tried)")
    verdict["claim_reproduced"] = bool(verdict["strict_locks_in_luck"] and verdict["rigor_smaller_gap"])
    out["verdict"] = verdict
    name = "e5_strict_luck" + ("_quick" if a.quick else "")
    out["figure"] = str(figure(out, name))
    write(name, out)
    print(json.dumps(verdict, indent=1))


def figure(out, name):
    from _common import RESULTS

    p = plt()
    fig, axes = p.subplots(1, 3, figsize=(14, 4))
    L = out["landscape"]
    for ax, key, title in zip(axes, ("false_keep_rate", "optimism_gap", "true_final"),
                              ("keeps whose TRUE delta <= 0", "optimism gap (honest - recorded)",
                               "true quality of final commit (lower=better)")):
        m = [L[r][key]["mean"] for r in RULES]
        err = [[m[i] - L[r][key]["lo"] for i, r in enumerate(RULES)], [L[r][key]["hi"] - m[i] for i, r in enumerate(RULES)]]
        ax.bar(range(len(RULES)), m, yerr=err, color=["#d93025", "#1e8e3e", "#81c995", "#1a73e8", "#fbbc04"])
        ax.set_xticks(range(len(RULES)))
        ax.set_xticklabels(RULES, rotation=20, fontsize=8)
        ax.set_title(title, fontsize=9)
        if key == "true_final":
            ax.set_ylim(min(m) - 0.02, max(m) + 0.01)
    fig.suptitle(f"E5 landscape: keep rules at equal total training runs ({out['config']['max_runs']}), "
                 f"{out['config']['landscape_seeds']} seeds, 95% CI", fontsize=10)
    fig.tight_layout()
    path = RESULTS / f"{name}.png"
    RESULTS.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=110)
    return path


if __name__ == "__main__":
    main()
