"""E2 - a fixed time budget makes changes comparable, and favours small/fast models on
*this* machine.

Claims [doc]: "A fixed time budget makes every experiment directly comparable" and
"A five-minute budget rewards whatever trains best in five minutes on that machine.
Smaller, faster models tend to win, which may not hold for longer runs." MLX port:
"some of those Mac Mini findings did not carry cleanly onto the Max baseline".

A. Landscape (exact truth, 30 seeds x 100 experiments):
   * wall-clock budget vs token budget (ablation): relative model size of the final
     kept config (baseline = 1.0);
   * the final config vs the baseline re-evaluated (noise-free) at 1x, 4x and 16x the
     budget: does the kept gain shrink or reverse at longer horizons?
   * "different machine": nights run at 0.5x compute ("Mac Mini") vs 2x ("Max"); the
     slow machine's winner evaluated on the fast machine vs the fast machine's own winner.
B. tinylm (real training): nights under a 2 s wall-clock budget vs a fixed token budget
   (the bytes the baseline sees in 2 s); parameter count of the kept models; the
   wall-clock winners and the baseline re-trained at 4x the budget.

Usage: python experiments/autoresearch/e2_budget_bias.py [--seeds N] [--quick] [--llm ...]
"""
from __future__ import annotations

from _common import SCRATCH, ci, parser, plt, pool_map, write  # noqa: I001

import json

import numpy as np

from rsi.autoresearch import AutoresearchLoop, Config, LandscapeTask, MockResearchAgent, Reeval, landscape_edit_pool


def ls_night(args):
    kind, compute, seed, n = args
    task = LandscapeTask(seed=seed, budget_kind=kind, compute=compute)
    res = AutoresearchLoop(task, MockResearchAgent(landscape_edit_pool(), seed=seed),
                           Config(max_experiments=n, persist=False, plot=False, hidden_audit=False, seed=seed),
                           out_dir=SCRATCH / "e2" / "ls").run()
    mod = task._module(task.files["prepare.py"])
    k = dict(mod.DEFAULTS)
    k.update(task.knobs(res.best))
    return {"kind": kind, "compute": compute, "seed": seed, "rel_size": mod.rel_size(k), "final": res.best,
            "knobs": {n_: k[n_] for n_ in mod.KNOBS}}


def truth_at(art, compute, kind="wallclock"):
    return LandscapeTask(compute=compute, budget_kind=kind).truth(art)


def tinylm_night(args):
    kind, seed, n, amount = args
    from rsi.domains.tinylm import TinyLMTask

    task = TinyLMTask(budget_s=amount, budget_kind=kind, kill_after=60.0)
    res = AutoresearchLoop(task, MockResearchAgent(task.mock_edit_pool(), seed=seed),
                           Config(max_experiments=n, hidden_audit=False, seed=seed, overwrite=True, plot=False,
                                  tag=f"e2-{kind}-{seed}"),
                           out_dir=SCRATCH / "e2" / f"tinylm_{kind}_{seed}").run()
    kept = [nd for nd in res.ledger.nodes() if nd.status == "keep"]
    return {"kind": kind, "seed": seed, "params_M_base": kept[0].meta["summary"].get("num_params_M"),
            "params_M_final": kept[-1].meta["summary"].get("num_params_M"),
            "steps_base": kept[0].meta["summary"].get("num_steps"), "steps_final": kept[-1].meta["summary"].get("num_steps"),
            "kept": [nd.change for nd in kept[1:]], "final_files": res.best.files, "base_files": res.baseline.files,
            "recorded_gain": res.meta["analysis"]["improvement"]}


def retrain(args):
    files, budget_s, seeds = args
    from rsi.core import Artifact
    from rsi.domains.tinylm import TinyLMTask

    task = TinyLMTask(budget_s=budget_s, kill_after=4 * budget_s + 20)
    return Reeval(task).run(Artifact(files), seeds)["mean"]


def main():
    ap = parser(__doc__.splitlines()[0], seeds=2)
    a = ap.parse_args()
    seeds = list(range(6 if a.quick else 30))
    n = 40 if a.quick else 100
    nights = pool_map(ls_night, [(k, 1.0, s, n) for k in ("wallclock", "tokens") for s in seeds], a.workers)
    out = {"config": {"landscape_seeds": len(seeds), "experiments": n}, "landscape": {}}
    for k in ("wallclock", "tokens"):
        rs = [r for r in nights if r["kind"] == k]
        out["landscape"][k] = {"final_rel_size": ci([r["rel_size"] for r in rs]),
                               "share_smaller_than_baseline": float(np.mean([r["rel_size"] < 1.0 for r in rs]))}
    wc = [r for r in nights if r["kind"] == "wallclock"]
    base = LandscapeTask().seed_artifact()
    horizon = {}
    for c in (1.0, 4.0, 16.0):
        tb = truth_at(base, c)
        horizon[f"{c:g}x"] = ci([tb - truth_at(r["final"], c) for r in wc])
    out["landscape"]["gain_vs_horizon"] = horizon
    slow = pool_map(ls_night, [("wallclock", 0.5, s, n) for s in seeds], a.workers)
    fast = pool_map(ls_night, [("wallclock", 2.0, s, n) for s in seeds], a.workers)
    tb_fast = truth_at(base, 2.0)
    out["landscape"]["machines"] = {
        "slow_winner_rel_size": ci([r["rel_size"] for r in slow]),
        "fast_winner_rel_size": ci([r["rel_size"] for r in fast]),
        "fast_machine_gain_of_slow_winner": ci([tb_fast - truth_at(r["final"], 2.0) for r in slow]),
        "fast_machine_gain_of_fast_winner": ci([tb_fast - truth_at(r["final"], 2.0) for r in fast]),
        "slow_machine_gain_of_slow_winner": ci([truth_at(base, 0.5) - truth_at(r["final"], 0.5) for r in slow])}
    # B: tinylm
    t_seeds = list(range(1 if a.quick else a.seeds))
    tn = 8 if a.quick else 20
    tok_budget = 100_000.0                 # ~ bytes the baseline sees in 2 s on this machine
    tl = pool_map(tinylm_night, [(k, s, tn, 2.0 if k == "wallclock" else tok_budget)
                                 for k in ("wallclock", "tokens") for s in t_seeds], a.workers)
    out["tinylm"] = {k: {"runs": [{x: r[x] for x in r if not x.endswith("files")} for r in tl if r["kind"] == k],
                         "params_ratio_final_over_base": ci([r["params_M_final"] / r["params_M_base"]
                                                             for r in tl if r["kind"] == k])}
                     for k in ("wallclock", "tokens")}
    wc_t = [r for r in tl if r["kind"] == "wallclock"]
    rs_seeds = [20_000, 20_001]
    jobs = [(r["final_files"], b, rs_seeds) for r in wc_t for b in (2.0, 8.0)] + \
           [(wc_t[0]["base_files"], b, rs_seeds) for b in (2.0, 8.0)]
    vals = pool_map(retrain, jobs, a.workers)
    base1, base4 = vals[-2], vals[-1]
    g1 = [base1 - vals[2 * i] for i in range(len(wc_t))]
    g4 = [base4 - vals[2 * i + 1] for i in range(len(wc_t))]
    out["tinylm"]["horizon"] = {"baseline_bpb_1x": base1, "baseline_bpb_4x": base4, "gain_1x": ci(g1),
                                "gain_4x": ci(g4)}
    L = out["landscape"]
    verdict = {
        "landscape_final_size_wallclock": L["wallclock"]["final_rel_size"]["mean"],
        "landscape_final_size_tokens": L["tokens"]["final_rel_size"]["mean"],
        "kept_configs_shrink_under_wallclock": bool(L["wallclock"]["final_rel_size"]["mean"] < 1.0 <
                                                    L["tokens"]["final_rel_size"]["mean"] + 1e-9),
        "landscape_gain_1x_4x_16x": [horizon[k]["mean"] for k in ("1x", "4x", "16x")],
        "gain_shrinks_at_longer_horizon": bool(horizon["16x"]["mean"] < horizon["1x"]["mean"]),
        "slow_winner_on_fast_machine_vs_fast_winner": [L["machines"]["fast_machine_gain_of_slow_winner"]["mean"],
                                                       L["machines"]["fast_machine_gain_of_fast_winner"]["mean"]],
        "tinylm_params_ratio_wallclock_vs_tokens": [out["tinylm"]["wallclock"]["params_ratio_final_over_base"]["mean"],
                                                    out["tinylm"]["tokens"]["params_ratio_final_over_base"]["mean"]],
        "tinylm_gain_1x_vs_4x": [out["tinylm"]["horizon"]["gain_1x"]["mean"], out["tinylm"]["horizon"]["gain_4x"]["mean"]],
    }
    verdict["claim_reproduced"] = bool(verdict["kept_configs_shrink_under_wallclock"] and
                                       verdict["gain_shrinks_at_longer_horizon"])
    out["verdict"] = verdict
    name = "e2_budget_bias" + ("_quick" if a.quick else "")
    out["figure"] = str(figure(out, name))
    write(name, out)
    print(json.dumps(verdict, indent=1))


def figure(out, name):
    from _common import RESULTS

    p = plt()
    fig, axes = p.subplots(1, 2, figsize=(12, 4.2))
    L = out["landscape"]
    ax = axes[0]
    ks = ("wallclock", "tokens")
    ax.bar(range(2), [L[k]["final_rel_size"]["mean"] for k in ks],
           yerr=[[L[k]["final_rel_size"]["mean"] - L[k]["final_rel_size"]["lo"] for k in ks],
                 [L[k]["final_rel_size"]["hi"] - L[k]["final_rel_size"]["mean"] for k in ks]],
           color=["#d93025", "#1a73e8"])
    ax.axhline(1.0, color="grey", ls="--", label="baseline size")
    ax.set_xticks(range(2))
    ax.set_xticklabels(["fixed wall-clock budget", "fixed token budget"])
    ax.set_ylabel("relative model size of the final kept config")
    ax.legend(fontsize=8)
    ax = axes[1]
    hs = ["1x", "4x", "16x"]
    ax.errorbar(range(3), [L["gain_vs_horizon"][h]["mean"] for h in hs],
                yerr=[[L["gain_vs_horizon"][h]["mean"] - L["gain_vs_horizon"][h]["lo"] for h in hs],
                      [L["gain_vs_horizon"][h]["hi"] - L["gain_vs_horizon"][h]["mean"] for h in hs]], marker="o")
    ax.axhline(0, color="grey", lw=0.8)
    ax.set_xticks(range(3))
    ax.set_xticklabels([f"{h} budget" for h in hs])
    ax.set_ylabel("true gain of the kept config over baseline")
    ax.set_title("landscape: gains found under the 1x budget, re-evaluated at longer horizons", fontsize=9)
    fig.tight_layout()
    path = RESULTS / f"{name}.png"
    RESULTS.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=110)
    return path


if __name__ == "__main__":
    main()
