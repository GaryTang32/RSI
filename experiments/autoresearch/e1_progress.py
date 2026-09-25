"""E1 - steady unattended progress (propose -> run -> score -> keep/reset).

Claim [doc]: "a coding agent can make steady, unattended progress on real ML work
when experiments are comparable and the grader is out of reach."

Setup
  * tinylm (real numpy LM training, fixed 2 s wall-clock budget, hardened mode),
    N experiments per night, scripted greedy research agent (``--llm claude:haiku``
    swaps in the live LLM agent);
  * baselines at equal experiment count: (i) the noise band of the unmodified
    train.py (k=5 re-runs), (ii) random search = best of N independent random configs;
  * the same loop on the known-truth landscape (many seeds) and on the generic
    AgentQA Domain (harness edits, SimModel) to show it is not LM-specific.
Confirming outcome: final best beyond the noise band, honest fresh-seed re-eval
confirms it, greedy >= random search, crashes handled without a human.

Usage: python experiments/autoresearch/e1_progress.py [--llm sim|claude:haiku] [--seeds N] [--quick]
"""
from __future__ import annotations

from _common import SCRATCH, ci, parser, plt, pool_map, propose_llm, running_best, write  # noqa: I001

import numpy as np

from rsi.autoresearch import (AutoresearchLoop, Config, LandscapeTask, LLMResearchAgent, MockResearchAgent,
                              NoiseCalibrator, RandomSearchAgent, landscape_edit_pool)
from rsi.core import RewriteEditor
from rsi.domains.tinylm import TinyLMTask

BUDGET_S = 2.0


def tinylm_arm(args):
    arm, seed, n, llm_spec = args
    task = TinyLMTask(budget_s=BUDGET_S)
    if arm == "greedy":
        agent = MockResearchAgent(task.mock_edit_pool(), seed=seed, crash_rate=0.05)
    elif arm == "random":
        agent = RandomSearchAgent(task.mock_edit_pool(), task.seed_artifact(), seed=seed)
    else:
        agent = LLMResearchAgent(RewriteEditor(propose_llm(llm_spec)))
    llms = [agent.editor.llm] if arm == "llm" else []
    loop = AutoresearchLoop(task, agent, Config(max_experiments=n, reeval_seeds=3, hidden_audit=(arm != "random"), overwrite=True,
                                                seed=seed, tag=f"e1-{arm}-{seed}"),
                            out_dir=SCRATCH / "e1" / f"tinylm_{arm}_{seed}", llms=llms)
    res = loop.run()
    a = res.meta["analysis"]
    return {"arm": arm, "seed": seed, "baseline": a["baseline"], "best": a["best"], "improvement": a["improvement"],
            "keep_rate": a["keep_rate"], "n_keep": a["n_keep"], "n_crash": a["n_crash"],
            "experiments_per_hour": a.get("experiments_per_hour"), "reeval": res.meta.get("reeval"),
            "crash_kinds": res.meta["crash_kinds"], "counters": res.meta["counters"],
            "top_hits": a["top_hits"][:5], "trajectory": [{k: r[k] for k in ("exp", "status", "metric", "description")}
                                                         for r in res.trajectory],
            "audit_final": (res.meta.get("audit") or [{}])[-1], "usage": res.usage.get("_total"),
            "results_tsv": res.meta["results_tsv"]}


def noise_band(k: int = 5) -> dict:
    task = TinyLMTask(budget_s=BUDGET_S)
    task.prepare()
    vals = [task.run(task.seed_artifact(), seed=s, mode="hardened").metric for s in range(k)]
    est = NoiseCalibrator(k).estimate(vals)
    return {"values": vals, "mean": float(np.mean(vals)), "sd": float(np.std(vals, ddof=1)), "delta": est.delta}


def landscape_arm(args):
    arm, seed, n = args
    task = LandscapeTask(seed=seed)
    agent = MockResearchAgent(landscape_edit_pool(), seed=seed) if arm == "greedy" else \
        RandomSearchAgent(landscape_edit_pool(), task.seed_artifact(), seed=seed, max_edits=4)
    res = AutoresearchLoop(task, agent, Config(max_experiments=n, persist=False, plot=False, hidden_audit=False,
                                               seed=seed), out_dir=SCRATCH / "e1" / f"ls_{arm}_{seed}").run()
    return {"arm": arm, "seed": seed, "truth_base": task.truth(res.baseline), "truth_final": task.truth(res.best),
            "recorded_best": res.meta["analysis"]["best"], "keep_rate": res.meta["analysis"]["keep_rate"],
            "curve": running_best(res.trajectory)}


def agentqa_arm(seed: int, n: int) -> dict:
    from rsi.autoresearch import run
    from rsi.autoresearch.pools import harness_edit_pool
    from rsi.domains.agentqa import AgentQADomain, SimModel

    dom = AgentQADomain()
    res = run(dom, dom.seed_artifact(), llm_task=SimModel(dom.tasks),
              agent=MockResearchAgent(harness_edit_pool(), seed=seed),
              config=Config(max_experiments=n, plot=False, seed=seed, run_seed=seed, overwrite=True),
              out_dir=SCRATCH / "e1" / f"agentqa_{seed}", task_kwargs={"k": 2})
    aud = res.meta["audit"]
    return {"seed": seed, "evolve_base": aud[0]["metric"], "evolve_final": aud[-1]["metric"],
            "holdout_base": aud[0]["holdout"], "holdout_final": aud[-1]["holdout"], "ood_base": aud[0]["ood"],
            "ood_final": aud[-1]["ood"], "kept": [r["description"] for r in aud[1:]]}


def main():
    ap = parser(__doc__.splitlines()[0])
    a = ap.parse_args()
    n = 8 if a.quick else 30
    live = a.llm != "sim"
    seeds = list(range(a.seeds))
    band = noise_band(3 if a.quick else 5)
    arms = ["llm"] if live else ["greedy", "random"]
    runs = pool_map(tinylm_arm, [(arm, s, n, a.llm) for arm in arms for s in seeds], 1 if live else a.workers)
    out = {"config": {"task": "tinylm", "budget_s": BUDGET_S, "mode": "hardened", "n_experiments": n,
                      "seeds": seeds, "llm": a.llm, "keep_rule": "strict"}, "noise_band": band, "tinylm": {}}
    for arm in arms:
        rs = [r for r in runs if r["arm"] == arm]
        out["tinylm"][arm] = {
            "improvement": ci([r["improvement"] for r in rs]),
            "honest_improvement": ci([r["reeval"]["baseline"]["mean"] - r["reeval"]["final"]["mean"] for r in rs]),
            "keep_rate": ci([r["keep_rate"] for r in rs]),
            "experiments_per_hour": ci([r["experiments_per_hour"] for r in rs]),
            "runs": rs}
    verdict = {}
    g = out["tinylm"][arms[0]]
    verdict["beyond_noise_band"] = bool(g["honest_improvement"]["lo"] > band["delta"])
    if not live:
        rnd = out["tinylm"]["random"]
        verdict["greedy_ge_random"] = bool(g["improvement"]["mean"] >= rnd["improvement"]["mean"])
        verdict["greedy_minus_random_honest"] = g["honest_improvement"]["mean"] - rnd["honest_improvement"]["mean"]
        ls_seeds = list(range(8 if a.quick else 30))
        ls = pool_map(landscape_arm, [(arm, s, 30 if a.quick else 100) for arm in ("greedy", "random")
                                      for s in ls_seeds], a.workers)
        out["landscape"] = {}
        for arm in ("greedy", "random"):
            rs = [r for r in ls if r["arm"] == arm]
            out["landscape"][arm] = {"true_gain": ci([r["truth_base"] - r["truth_final"] for r in rs]),
                                     "keep_rate": ci([r["keep_rate"] for r in rs]),
                                     "mean_curve": np.nanmean([r["curve"] for r in rs], axis=0).tolist()}
        verdict["landscape_greedy_ge_random"] = bool(out["landscape"]["greedy"]["true_gain"]["mean"] >=
                                                     out["landscape"]["random"]["true_gain"]["mean"])
        aq = [agentqa_arm(s, 6 if a.quick else 10) for s in seeds]
        out["agentqa"] = {"runs": aq, "holdout_gain": ci([r["holdout_final"] - r["holdout_base"] for r in aq]),
                          "ood_gain": ci([r["ood_final"] - r["ood_base"] for r in aq])}
        verdict["generic_domain_transfers"] = bool(out["agentqa"]["holdout_gain"]["mean"] > 0)
    verdict["claim_reproduced"] = bool(verdict["beyond_noise_band"] and verdict.get("greedy_ge_random", True))
    out["verdict"] = verdict
    name = "e1_progress" + ("_live" if live else "") + ("_quick" if a.quick else "")
    out["figure"] = str(figure(out, arms, band, name))
    write(name, out)
    print(json.dumps(verdict, indent=1))


def figure(out, arms, band, name):
    p = plt()
    fig, axes = p.subplots(1, 2 if "landscape" in out else 1, figsize=(13 if "landscape" in out else 7, 4.5))
    axes = np.atleast_1d(axes)
    ax = axes[0]
    colors = {"greedy": "#1e8e3e", "random": "#e37400", "llm": "#1a73e8"}
    for arm in arms:
        for r in out["tinylm"][arm]["runs"]:
            curve = running_best(r["trajectory"])
            ax.step(range(len(curve)), curve, where="post", color=colors[arm], alpha=0.6,
                    label=arm if r["seed"] == 0 else None)
    ax.axhspan(band["mean"] - band["delta"], band["mean"] + band["delta"], color="grey", alpha=0.2,
               label="baseline noise band")
    ax.set_xlabel("experiment #")
    ax.set_ylabel("running best val_bpb (lower is better)")
    ax.set_title("tinylm: unattended loop vs random search")
    ax.legend(fontsize=8)
    if "landscape" in out:
        ax = axes[1]
        for arm in ("greedy", "random"):
            ax.plot(out["landscape"][arm]["mean_curve"], color=colors[arm], label=f"{arm} (mean of seeds)")
        ax.set_xlabel("experiment #")
        ax.set_ylabel("running best val_loss")
        ax.set_title("landscape (known truth)")
        ax.legend(fontsize=8)
    fig.tight_layout()
    from _common import RESULTS

    RESULTS.mkdir(parents=True, exist_ok=True)
    path = RESULTS / f"{name}.png"
    fig.savefig(path, dpi=110)
    return path


import json  # noqa: E402

if __name__ == "__main__":
    main()
