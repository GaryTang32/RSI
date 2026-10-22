"""E2 - a fixed time budget makes changes comparable and favours small, fast models on this machine.

Usage: python experiments/autoresearch/e2_budget_bias.py [--seeds N] [--quick] [--llm sim|claude:haiku]
"""
from _common import (SCRATCH, ci, is_live, parser, plt, pool_map, report, research_agent, save_fig,  # noqa: I001
                     slug, suffix, usage_of)

import numpy as np

from rsi.autoresearch import AutoresearchLoop, Config, LandscapeTask, MockResearchAgent, Reeval, landscape_edit_pool
from rsi.core import Artifact
from rsi.domains.tinylm import TinyLMTask

KINDS = ("wallclock", "tokens")
TOKEN_BUDGET = 100_000.0  # ~ bytes the baseline sees in 2 s on this machine


def ls_night(args) -> dict:
    kind, compute, seed, n = args
    task = LandscapeTask(seed=seed, budget_kind=kind, compute=compute)
    res = AutoresearchLoop(task, MockResearchAgent(landscape_edit_pool(), seed=seed),
                           Config(max_experiments=n, persist=False, plot=False, hidden_audit=False, seed=seed),
                           out_dir=SCRATCH / "e2" / "ls").run()
    mod = task._module(task.files["prepare.py"])
    k = {**mod.DEFAULTS, **task.knobs(res.best)}
    return {"kind": kind, "compute": compute, "seed": seed, "rel_size": mod.rel_size(k), "final": res.best,
            "knobs": {name: k[name] for name in mod.KNOBS}}


def truth_at(art, compute: float) -> float:
    return LandscapeTask(compute=compute, budget_kind="wallclock").truth(art)


def tinylm_night(args) -> dict:
    kind, seed, n, amount, llm_spec = args
    task = TinyLMTask(budget_s=amount, budget_kind=kind, kill_after=60.0)
    agent, llms = research_agent(llm_spec, task.mock_edit_pool(), seed=seed)
    cfg = Config(max_experiments=n, hidden_audit=False, seed=seed, overwrite=True, plot=False, tag=f"e2-{kind}-{seed}")
    res = AutoresearchLoop(task, agent, cfg, out_dir=SCRATCH / "e2" / f"tinylm_{kind}_{seed}_{slug(llm_spec)}",
                           llms=llms).run()
    kept = [nd for nd in res.ledger.nodes() if nd.status == "keep"]
    first, last = kept[0].meta["summary"], kept[-1].meta["summary"]
    return {"kind": kind, "seed": seed, "params_M_base": first.get("num_params_M"),
            "params_M_final": last.get("num_params_M"), "steps_base": first.get("num_steps"),
            "steps_final": last.get("num_steps"), "kept": [nd.change for nd in kept[1:]],
            "final_files": res.best.files, "base_files": res.baseline.files,
            "recorded_gain": res.meta["analysis"]["improvement"], "usage": usage_of(llms)}


def retrain(args) -> float:
    files, budget_s, seeds = args
    task = TinyLMTask(budget_s=budget_s, kill_after=4 * budget_s + 20)
    return Reeval(task).run(Artifact(files), seeds)["mean"]


def tinylm_part(t_seeds, tn: int, llm_spec: str, workers: int) -> dict:
    """Nights under a wall-clock vs a token budget; the wall-clock winners re-trained at 4x."""
    tl = pool_map(tinylm_night, [(k, s, tn, 2.0 if k == "wallclock" else TOKEN_BUDGET, llm_spec)
                                 for k in KINDS for s in t_seeds], workers)
    out = {k: {"runs": [{x: r[x] for x in r if not x.endswith("files")} for r in tl if r["kind"] == k],
               "params_ratio_final_over_base": ci([r["params_M_final"] / r["params_M_base"]
                                                   for r in tl if r["kind"] == k])}
           for k in KINDS}
    wc = [r for r in tl if r["kind"] == "wallclock"]
    rs_seeds = [20_000, 20_001]
    jobs = [(r["final_files"], b, rs_seeds) for r in wc for b in (2.0, 8.0)] + \
           [(wc[0]["base_files"], b, rs_seeds) for b in (2.0, 8.0)]
    vals = pool_map(retrain, jobs, workers)
    base1, base4 = vals[-2], vals[-1]
    out["horizon"] = {"baseline_bpb_1x": base1, "baseline_bpb_4x": base4,
                      "gain_1x": ci([base1 - vals[2 * i] for i in range(len(wc))]),
                      "gain_4x": ci([base4 - vals[2 * i + 1] for i in range(len(wc))])}
    return out


def tinylm_verdict(T: dict) -> dict:
    return {"tinylm_params_ratio_wallclock_vs_tokens": [T[k]["params_ratio_final_over_base"]["mean"] for k in KINDS],
            "tinylm_gain_1x_vs_4x": [T["horizon"]["gain_1x"]["mean"], T["horizon"]["gain_4x"]["mean"]]}


def main_live(a) -> None:
    t_seeds = list(range(a.seeds))
    tn = 4 if a.quick else 8
    T = tinylm_part(t_seeds, tn, a.llm, 1)
    verdict = {**tinylm_verdict(T), "kept": {k: [r["kept"] for r in T[k]["runs"]] for k in KINDS},
               "usage": [r["usage"] for k in KINDS for r in T[k]["runs"]]}
    report("e2_budget_bias" + suffix(a.llm, a.quick),
           {"config": {"tinylm_experiments": tn, "seeds": t_seeds, "llm": a.llm}, "tinylm": T, "verdict": verdict})


def main() -> None:
    a = parser(__doc__.splitlines()[0], seeds=2).parse_args()
    if is_live(a.llm):
        return main_live(a)
    seeds = list(range(6 if a.quick else 30))
    n = 40 if a.quick else 100
    nights = pool_map(ls_night, [(k, 1.0, s, n) for k in KINDS for s in seeds], a.workers)
    out = {"config": {"landscape_seeds": len(seeds), "experiments": n}, "landscape": {}}
    L = out["landscape"]
    for k in KINDS:
        rs = [r for r in nights if r["kind"] == k]
        L[k] = {"final_rel_size": ci([r["rel_size"] for r in rs]),
                "share_smaller_than_baseline": float(np.mean([r["rel_size"] < 1.0 for r in rs]))}
    wc = [r for r in nights if r["kind"] == "wallclock"]
    base = LandscapeTask().seed_artifact()
    horizon = {}
    for c in (1.0, 4.0, 16.0):
        tb = truth_at(base, c)
        horizon[f"{c:g}x"] = ci([tb - truth_at(r["final"], c) for r in wc])
    L["gain_vs_horizon"] = horizon
    slow = pool_map(ls_night, [("wallclock", 0.5, s, n) for s in seeds], a.workers)
    fast = pool_map(ls_night, [("wallclock", 2.0, s, n) for s in seeds], a.workers)
    tb_fast = truth_at(base, 2.0)
    L["machines"] = {
        "slow_winner_rel_size": ci([r["rel_size"] for r in slow]),
        "fast_winner_rel_size": ci([r["rel_size"] for r in fast]),
        "fast_machine_gain_of_slow_winner": ci([tb_fast - truth_at(r["final"], 2.0) for r in slow]),
        "fast_machine_gain_of_fast_winner": ci([tb_fast - truth_at(r["final"], 2.0) for r in fast]),
        "slow_machine_gain_of_slow_winner": ci([truth_at(base, 0.5) - truth_at(r["final"], 0.5) for r in slow])}
    out["tinylm"] = tinylm_part(list(range(1 if a.quick else a.seeds)), 8 if a.quick else 20, a.llm, a.workers)
    size = {k: L[k]["final_rel_size"]["mean"] for k in KINDS}
    verdict = {
        "landscape_final_size_wallclock": size["wallclock"],
        "landscape_final_size_tokens": size["tokens"],
        "kept_configs_shrink_under_wallclock": bool(size["wallclock"] < 1.0 < size["tokens"] + 1e-9),
        "landscape_gain_1x_4x_16x": [horizon[k]["mean"] for k in ("1x", "4x", "16x")],
        "gain_shrinks_at_longer_horizon": bool(horizon["16x"]["mean"] < horizon["1x"]["mean"]),
        "slow_winner_on_fast_machine_vs_fast_winner": [L["machines"]["fast_machine_gain_of_slow_winner"]["mean"],
                                                       L["machines"]["fast_machine_gain_of_fast_winner"]["mean"]],
        **tinylm_verdict(out["tinylm"])}
    verdict["claim_reproduced"] = bool(verdict["kept_configs_shrink_under_wallclock"] and
                                       verdict["gain_shrinks_at_longer_horizon"])
    out["verdict"] = verdict
    name = "e2_budget_bias" + ("_quick" if a.quick else "")
    out["figure"] = str(figure(out, name))
    report(name, out)


def figure(out: dict, name: str):
    p = plt()
    fig, axes = p.subplots(1, 2, figsize=(12, 4.2))
    L = out["landscape"]
    ax = axes[0]
    sizes = [L[k]["final_rel_size"] for k in KINDS]
    ax.bar(range(2), [s["mean"] for s in sizes], yerr=[[s["mean"] - s["lo"] for s in sizes],
                                                       [s["hi"] - s["mean"] for s in sizes]],
           color=["#d93025", "#1a73e8"])
    ax.axhline(1.0, color="grey", ls="--", label="baseline size")
    ax.set_xticks(range(2))
    ax.set_xticklabels(["fixed wall-clock budget", "fixed token budget"])
    ax.set_ylabel("relative model size of the final kept config")
    ax.legend(fontsize=8)
    ax = axes[1]
    gains = [L["gain_vs_horizon"][h] for h in ("1x", "4x", "16x")]
    ax.errorbar(range(3), [g["mean"] for g in gains],
                yerr=[[g["mean"] - g["lo"] for g in gains], [g["hi"] - g["mean"] for g in gains]], marker="o")
    ax.axhline(0, color="grey", lw=0.8)
    ax.set_xticks(range(3))
    ax.set_xticklabels([f"{h} budget" for h in ("1x", "4x", "16x")])
    ax.set_ylabel("true gain of the kept config over baseline")
    ax.set_title("landscape: gains found under the 1x budget, re-evaluated at longer horizons", fontsize=9)
    return save_fig(fig, name)


if __name__ == "__main__":
    main()
