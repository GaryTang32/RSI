"""E12 - non-LLM-model adaptation: tuning a gradient-boosted tree against a held-out metric.

Claim [doc]: "Non-LLM adaptations, such as tuning a gradient-boosted tree model against
a held-out metric, showing the loop isn't specific to language models."
Expected pattern from the xgboost port (spec section 7.4): CV and same-period hidden AUC
improve together; the time-shifted holdout improves less or regresses; feature
engineering contributes gains beyond hyperparameter search.

Tabular task (HistGradientBoosting, 5-fold CV AUC on 20k era-0 rows, 20 s ceiling per
run; hidden iid era-0 and shifted era-1 test sets scored post hoc for every keep):
  fe+hpo      scripted agent with feature-engineering and hyperparameter edits
  hpo-only    the same agent restricted to hyperparameter edits
  random-hpo  best of N random hyperparameter configurations (Optuna-style baseline)
at an equal experiment count, 3 seeds.

Usage: python experiments/autoresearch/e12_gbt.py [--seeds N] [--quick] [--llm sim|claude:haiku]
"""
from __future__ import annotations

from _common import suffix, SCRATCH, ci, parser, plt, pool_map, propose_llm, write  # noqa: I001

import json

from rsi.autoresearch import AutoresearchLoop, Config, LLMResearchAgent, MockResearchAgent, RandomSearchAgent
from rsi.core import RewriteEditor
from rsi.domains.tabular import TabularTask


def arm_run(args):
    arm, seed, n, llm_spec = args
    task = TabularTask()
    pool = task.mock_edit_pool()
    hpo = [e for e in pool if not e.name.startswith(("fe_", "col_")) and e.kind not in ("crash", "exploit")]
    llms = []
    if arm == "fe+hpo":
        agent = MockResearchAgent(pool, seed=seed, crash_rate=0.03)
    elif arm == "hpo-only":
        agent = MockResearchAgent(hpo, seed=seed)
    elif arm == "random-hpo":
        agent = RandomSearchAgent(hpo, task.seed_artifact(), seed=seed, max_edits=4)
    else:
        llm = propose_llm(llm_spec, pool=pool, seed=seed)
        agent, llms = LLMResearchAgent(RewriteEditor(llm)), [llm]
    res = AutoresearchLoop(task, agent, Config(max_experiments=n, seed=seed, overwrite=True, tag=f"e12-{arm}-{seed}"),
                           out_dir=SCRATCH / "e12" / f"{arm.replace('+', '_')}_{seed}", llms=llms).run()
    aud = [r for r in res.meta["audit"] if "audit_error" not in r]      # crashed audits are reported, not scored
    return {"arm": arm, "seed": seed, "keeps": [{"exp": r["exp"], "description": r["description"], "cv": r["metric"],
                                                  "iid": r["test_iid"], "shift": r["test_shift"]} for r in aud],
            "keep_rate": res.meta["analysis"]["keep_rate"], "n_crash": res.meta["analysis"]["n_crash"],
            "audit_errors": [r for r in res.meta["audit"] if "audit_error" in r],
            "over_budget": res.meta["budget_events"]["over_budget"],
            "rejected_or_violations": res.meta["crash_kinds"].get("violation", 0),
            "results_tsv": res.meta["results_tsv"], "usage": res.usage.get("_total")}


def main():
    ap = parser(__doc__.splitlines()[0], seeds=3)
    a = ap.parse_args()
    n = 8 if a.quick else 25
    seeds = list(range(1 if a.quick else a.seeds))
    arms = ["llm"] if a.llm != "sim" else ["fe+hpo", "hpo-only", "random-hpo"]
    runs = pool_map(arm_run, [(arm, s, n, a.llm) for arm in arms for s in seeds], 1 if a.llm != "sim" else a.workers)
    out = {"config": {"task": "tabular", "n_experiments": n, "seeds": seeds, "ceiling_s": 20, "llm": a.llm}}
    for arm in arms:
        rs = [r for r in runs if r["arm"] == arm]
        f = [r["keeps"][0] for r in rs]
        l_ = [r["keeps"][-1] for r in rs]
        out[arm] = {
            "cv_gain": ci([b["cv"] - x["cv"] for x, b in zip(f, l_)]),
            "iid_gain": ci([b["iid"] - x["iid"] for x, b in zip(f, l_)]),
            "shift_gain": ci([b["shift"] - x["shift"] for x, b in zip(f, l_)]),
            "final_iid": ci([b["iid"] for b in l_]), "final_shift": ci([b["shift"] for b in l_]),
            "keep_rate": ci([r["keep_rate"] for r in rs]),
            "cv_iid_step_agreement": ci([sum((k["cv"] > p["cv"]) == (k["iid"] > p["iid"]) for p, k in
                                             zip(r["keeps"], r["keeps"][1:])) / max(1, len(r["keeps"]) - 1)
                                         for r in rs]),
            "keeps_up_cv_iid_down_shift": [k["description"] for r in rs for p, k in zip(r["keeps"], r["keeps"][1:])
                                           if k["iid"] > p["iid"] and k["shift"] < p["shift"] - 0.005],
            "runs": rs}
    verdict = {}
    if "fe+hpo" in out:
        fe, hp, rnd = out["fe+hpo"], out["hpo-only"], out["random-hpo"]
        verdict = {
            "cv_and_iid_improve_together": bool(fe["cv_gain"]["mean"] > 0 and fe["iid_gain"]["mean"] > 0),
            "shift_improves_less": bool(fe["shift_gain"]["mean"] < fe["iid_gain"]["mean"]),
            "fe_beyond_hpo_iid": fe["iid_gain"]["mean"] - hp["iid_gain"]["mean"],
            "agent_hpo_vs_random_hpo_iid": hp["iid_gain"]["mean"] - rnd["iid_gain"]["mean"],
            "gains": {arm: {k: out[arm][k]["mean"] for k in ("cv_gain", "iid_gain", "shift_gain")} for arm in arms},
            "shift_trap_keeps": fe["keeps_up_cv_iid_down_shift"][:6]}
        verdict["claim_reproduced"] = bool(verdict["cv_and_iid_improve_together"] and verdict["shift_improves_less"]
                                           and verdict["fe_beyond_hpo_iid"] > 0)
    else:
        verdict = {"llm_gains": {k: out["llm"][k]["mean"] for k in ("cv_gain", "iid_gain", "shift_gain")}}
    out["verdict"] = verdict
    name = "e12_gbt" + suffix(a.llm, a.quick)
    out["figure"] = str(figure(out, arms, name))
    write(name, out)
    print(json.dumps(verdict, indent=1))


def figure(out, arms, name):
    from _common import RESULTS

    p = plt()
    fig, axes = p.subplots(1, 3, figsize=(15, 4.2), sharex=True)
    col = {"fe+hpo": "#1e8e3e", "hpo-only": "#1a73e8", "random-hpo": "#e37400", "llm": "#a142f4"}
    for ax, key, title in zip(axes, ("cv", "iid", "shift"),
                              ("CV AUC (the loop's metric)", "hidden iid test AUC", "hidden shifted test AUC")):
        for arm in arms:
            for r in out[arm]["runs"]:
                ax.step([k["exp"] for k in r["keeps"]] + [out["config"]["n_experiments"]],
                        [k[key] for k in r["keeps"]] + [r["keeps"][-1][key]], where="post", color=col[arm],
                        alpha=0.7, label=arm if r["seed"] == 0 else None)
        ax.set_title(title, fontsize=10)
        ax.set_xlabel("experiment #")
    axes[0].legend(fontsize=8)
    fig.suptitle("E12: autoresearch on gradient-boosted trees (kept chain, per seed)", fontsize=10)
    fig.tight_layout()
    path = RESULTS / f"{name}.png"
    RESULTS.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=110)
    return path


if __name__ == "__main__":
    main()
