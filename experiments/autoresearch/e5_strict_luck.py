"""E5 - "strictly better" on one noisy number locks in luck; keep rules compared at equal total training runs.

Usage: python experiments/autoresearch/e5_strict_luck.py [--seeds N] [--quick] [--llm sim|claude:haiku]
"""
from _common import (SCRATCH, ci, is_live, parser, plt, pool_map, report, research_agent, save_fig,  # noqa: I001
                     suffix, usage_of)

import numpy as np

from rsi.autoresearch import (AutoresearchLoop, BootstrapRigorKeep, Config, GateKeep, LandscapeTask, MockResearchAgent,
                              Reeval, SimplicityWeighted, StrictKeep, UpstreamKeep, landscape_edit_pool)
from rsi.core import MinGain
from rsi.domains.tinylm import TinyLMTask

RULES = ("strict", "upstream", "rigor", "rigor_pinned", "noise_band", "simplicity")
TINYLM_RULES = ("strict", "upstream", "rigor")
LS_KEYS = ["n_keeps", "false_keep_rate", "n_false_keeps", "seed_keeps", "optimism_gap", "true_final", "true_gain",
           "n_experiments"]


def make_rule(name: str):
    rules = {"strict": StrictKeep,
             "upstream": UpstreamKeep,  # the faithful default (strict + simplicity + VRAM soft constraint)
             "rigor": BootstrapRigorKeep,
             "rigor_pinned": lambda: BootstrapRigorKeep(vary_seed=False),
             "noise_band": lambda: GateKeep(MinGain(), name="noise_band"),
             "simplicity": lambda: SimplicityWeighted(eps=0.001)}
    if name not in rules:
        raise ValueError(name)
    return rules[name]()


def landscape_arm(args) -> dict:
    rule, seed, max_runs, *rest = args
    llm_spec = rest[0] if rest else "sim"
    task = LandscapeTask(seed=seed)
    agent, llms = research_agent(llm_spec, landscape_edit_pool(), seed=seed)
    cfg = Config(max_experiments=None, max_runs=max_runs, persist=False, plot=False, hidden_audit=False, seed=seed,
                 noise_runs=5 if rule == "noise_band" else 0)
    loop = AutoresearchLoop(task, agent, cfg, keep_rule=make_rule(rule), out_dir=SCRATCH / "e5" / "ls", llms=llms)
    res = loop.run()
    keeps = [nd for nd in res.ledger.nodes() if nd.status == "keep"]
    true_d = [keeps[i - 1].metrics["truth"] - keeps[i].metrics["truth"] for i in range(1, len(keeps))]
    honest = Reeval(task).run(res.best, [50_000 + i for i in range(5)])
    rec = float(np.mean(loop.inc["samples"].values))
    return {"rule": rule, "seed": seed, "n_keeps": len(keeps) - 1, "n_runs": loop.n_runs,
            "n_experiments": loop.n_experiments,
            "false_keep_rate": float(np.mean([d <= 0 for d in true_d])) if true_d else 0.0,
            "n_false_keeps": int(sum(d <= 0 for d in true_d)),
            "seed_keeps": sum(1 for nd in keeps if nd.meta.get("edit") == "seed_change"),
            "recorded_best": rec, "honest_final": honest["mean"], "optimism_gap": honest["mean"] - rec,
            "true_final": task.truth(res.best), "true_gain": task.truth(res.baseline) - task.truth(res.best),
            "kept": [nd.change for nd in keeps[1:]], "usage": usage_of(llms)}


def tinylm_arm(args) -> dict:
    rule, seed, max_runs = args
    task = TinyLMTask(budget_s=2.0)
    cfg = Config(max_experiments=None, max_runs=max_runs, hidden_audit=False, seed=seed, overwrite=True,
                 reeval_seeds=5, tag=f"e5-{rule}-{seed}")
    loop = AutoresearchLoop(task, MockResearchAgent(task.mock_edit_pool(), seed=seed), cfg, keep_rule=make_rule(rule),
                            out_dir=SCRATCH / "e5" / f"tinylm_{rule}_{seed}")
    res = loop.run()
    keeps = [nd for nd in res.ledger.nodes() if nd.status == "keep"]
    rv = res.meta["reeval"]
    return {"rule": rule, "seed": seed, "n_keeps": len(keeps) - 1, "n_runs": loop.n_runs,
            "n_experiments": loop.n_experiments,
            "seed_keeps": sum(1 for nd in keeps if nd.meta.get("edit") == "seed_change"),
            "recorded_best": rv["recorded_best"], "honest_final": rv["final"]["mean"],
            "honest_baseline": rv["baseline"]["mean"], "optimism_gap": rv["optimism_gap"],
            "honest_gain": rv["baseline"]["mean"] - rv["final"]["mean"], "kept": [nd.change for nd in keeps[1:]]}


def agg(rows: list[dict], keys) -> dict:
    return {k: ci([r[k] for r in rows]) for k in keys}


def main_live(a) -> None:
    rules = ("strict", "rigor")
    max_runs = 12 if a.quick else 30
    seeds = list(range(a.seeds))
    ls = [landscape_arm((r, s, max_runs, a.llm)) for r in rules for s in seeds]
    L = {r: {**agg([x for x in ls if x["rule"] == r], LS_KEYS), "runs": [x for x in ls if x["rule"] == r]}
         for r in rules}
    verdict = {f"{r}_{k}": L[r][k]["mean"] for r in rules
               for k in ("false_keep_rate", "optimism_gap", "true_final", "n_experiments")}
    verdict["usage"] = [x["usage"] for x in ls]
    report("e5_strict_luck" + suffix(a.llm, a.quick),
           {"config": {"max_runs": max_runs, "seeds": seeds, "rules": rules, "llm": a.llm}, "landscape": L,
            "verdict": verdict})


def main() -> None:
    a = parser(__doc__.splitlines()[0], seeds=3).parse_args()
    if is_live(a.llm):
        return main_live(a)
    ls_seeds = list(range(8 if a.quick else 50))
    max_runs = 60 if a.quick else 150
    ls = pool_map(landscape_arm, [(r, s, max_runs) for r in RULES for s in ls_seeds], a.workers)
    out = {"config": {"landscape_seeds": len(ls_seeds), "max_runs": max_runs, "rules": RULES,
                      "noise": {"seed_sd": 0.002, "nondeterminism_sd": 0.001, "val_sd": 0.0015}},
           "landscape": {r: agg([x for x in ls if x["rule"] == r], LS_KEYS) for r in RULES}}
    t_seeds = list(range(1 if a.quick else a.seeds))
    tl = pool_map(tinylm_arm, [(r, s, 10 if a.quick else 27) for r in TINYLM_RULES for s in t_seeds], a.workers)
    out["tinylm"] = {r: {**agg([x for x in tl if x["rule"] == r],
                               ["n_keeps", "seed_keeps", "optimism_gap", "honest_gain", "n_experiments"]),
                         "runs": [x for x in tl if x["rule"] == r]} for r in TINYLM_RULES}
    L, T = out["landscape"], out["tinylm"]
    verdict = {
        "strict_false_keep_rate": L["strict"]["false_keep_rate"]["mean"],
        "rigor_false_keep_rate": L["rigor"]["false_keep_rate"]["mean"],
        "strict_seed_keeps_per_run": L["strict"]["seed_keeps"]["mean"],
        "rigor_seed_keeps_per_run": L["rigor"]["seed_keeps"]["mean"],
        "strict_optimism_gap": L["strict"]["optimism_gap"]["mean"],
        "rigor_optimism_gap": L["rigor"]["optimism_gap"]["mean"],
        "strict_optimism_gap_ci_lo": L["strict"]["optimism_gap"]["lo"],
        "true_final_strict": L["strict"]["true_final"]["mean"], "true_final_rigor": L["rigor"]["true_final"]["mean"],
        "tinylm_strict_optimism_gap": T["strict"]["optimism_gap"]["mean"],
        "tinylm_rigor_optimism_gap": T["rigor"]["optimism_gap"]["mean"],
        "upstream_false_keep_rate": L["upstream"]["false_keep_rate"]["mean"],
        "upstream_optimism_gap": L["upstream"]["optimism_gap"]["mean"],
        "true_final_upstream": L["upstream"]["true_final"]["mean"],
        "tinylm_upstream_optimism_gap": T["upstream"]["optimism_gap"]["mean"],
    }
    verdict["strict_locks_in_luck"] = bool(
        L["strict"]["optimism_gap"]["lo"] > 0 and
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
    report(name, out)


def figure(out: dict, name: str):
    p = plt()
    fig, axes = p.subplots(1, 3, figsize=(14, 4))
    L = out["landscape"]
    for ax, key, title in zip(axes, ("false_keep_rate", "optimism_gap", "true_final"),
                              ("keeps whose TRUE delta <= 0", "optimism gap (honest - recorded)",
                               "true quality of final commit (lower=better)")):
        m = [L[r][key]["mean"] for r in RULES]
        err = [[m[i] - L[r][key]["lo"] for i, r in enumerate(RULES)],
               [L[r][key]["hi"] - m[i] for i, r in enumerate(RULES)]]
        ax.bar(range(len(RULES)), m, yerr=err, color=["#d93025", "#1e8e3e", "#81c995", "#1a73e8", "#fbbc04"])
        ax.set_xticks(range(len(RULES)))
        ax.set_xticklabels(RULES, rotation=20, fontsize=8)
        ax.set_title(title, fontsize=9)
        if key == "true_final":
            ax.set_ylim(min(m) - 0.02, max(m) + 0.01)
    fig.suptitle(f"E5 landscape: keep rules at equal total training runs ({out['config']['max_runs']}), "
                 f"{out['config']['landscape_seeds']} seeds, 95% CI", fontsize=10)
    return save_fig(fig, name)


if __name__ == "__main__":
    main()
