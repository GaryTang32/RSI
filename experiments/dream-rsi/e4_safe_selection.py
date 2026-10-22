"""E4: with the incumbent as a candidate the selected policy never regresses on replay; ablation: revisions only.

    python experiments/dream-rsi/e4_safe_selection.py [--seeds N] [--quick]
"""
import numpy as np
from _common import developer_of, domain_of, figure, fmt, paired, parse_args, pmap, sandbox_of, save, save_figure, summ

from rsi.dream import Config, run


def one(job):
    seed, incl, sigma, rounds, llm = job
    cfg = Config(rounds=rounds, W=4, branch_count=6, refine_count=4, M=4, include_incumbent=incl,
                 sandbox=sandbox_of(llm), seed=seed, agent_workers=1)
    res = run(domain_of("synthetic", seed), config=cfg, developer=developer_of(llm, sigma=sigma))
    tr = res.trajectory
    deltas, online = [], []
    for i, r in enumerate(tr):
        if "dream" in r:
            deltas.append(r["dream"]["delta_vs_incumbent"])
            if i + 1 < len(tr):  # raw Eq. 1 of the next live search
                n = tr[i + 1]
                online.append(n["round_best"] - n["root"] - 0.01 * n["N"] + 0.005 * n["N"] / max(1, n["k"]))
    return {"seed": seed, "include_incumbent": incl, "sigma": sigma, "deltas": deltas, "next_online_value": online,
            "final_best": res.meta["best_score"], "calls": res.usage["_cost"]["agent_calls"],
            "selected": [r["dream"]["selected"] for r in tr if "dream" in r]}


def main():
    a = parse_args("E4 safe selection", default_seeds=15)
    rounds = 4 if a.quick else 6
    out = {}
    for sigma in (0.2, 0.6):
        rows = pmap(one, [(s, incl, sigma, rounds, a.llm) for s in range(a.seeds) for incl in (True, False)], a.workers)
        inc = [r for r in rows if r["include_incumbent"]]
        exc = [r for r in rows if not r["include_incumbent"]]
        d_inc = [d for r in inc for d in r["deltas"]]
        d_exc = [d for r in exc for d in r["deltas"]]
        fb_exc = [r["final_best"] for r in sorted(exc, key=lambda r: r["seed"])]
        fb_inc = [r["final_best"] for r in sorted(inc, key=lambda r: r["seed"])]
        res = {
            "phases": len(d_inc), "min_delta_with_incumbent": float(min(d_inc)),
            "regressions_with_incumbent": int(sum(d < -1e-12 for d in d_inc)),
            "regressions_without_incumbent": int(sum(d < -1e-12 for d in d_exc)),
            "regression_rate_without": float(np.mean([d < -1e-12 for d in d_exc])),
            "mean_delta_with": summ(d_inc), "mean_delta_without": summ(d_exc),
            "worst_delta_without": float(min(d_exc)),
            "incumbent_kept_rate": float(np.mean([s == 0 for r in inc for s in r["selected"]])),
            "next_online_value_with": summ([v for r in inc for v in r["next_online_value"]]),
            "next_online_value_without": summ([v for r in exc for v in r["next_online_value"]]),
            "final_best_with": summ([r["final_best"] for r in inc]),
            "final_best_without": summ([r["final_best"] for r in exc]),
            "final_best_diff_with_minus_without": paired(fb_exc, fb_inc),
            "calls_with": summ([r["calls"] for r in inc]), "calls_without": summ([r["calls"] for r in exc]),
            "rows": rows}
        out[f"sigma_{sigma}"] = res
        print(f"[developer sigma {sigma}] {res['phases']} dreaming phases")
        print(f"   with incumbent: min V*-V0 = {res['min_delta_with_incumbent']:+.4f}, regressions "
              f"{res['regressions_with_incumbent']}; incumbent kept {res['incumbent_kept_rate']:.0%}")
        print(f"   without: regressions {res['regressions_without_incumbent']}/{len(d_exc)} "
              f"(worst {res['worst_delta_without']:+.4f}); mean V*-V0 {fmt(res['mean_delta_without'])}")
        print(f"   next live search value: with {fmt(res['next_online_value_with'])} vs without "
              f"{fmt(res['next_online_value_without'])}; final best with {fmt(res['final_best_with'])} vs without "
              f"{fmt(res['final_best_without'])}")
    plt, png = figure("e4_safe_selection")
    fig, ax = plt.subplots(figsize=(6, 3.5))
    data, labels = [], []
    for k, v in out.items():
        for incl in (True, False):
            data.append([d for r in v["rows"] if r["include_incumbent"] == incl for d in r["deltas"]])
            labels.append(f"{'with' if incl else 'without'}\n{k}")
    ax.boxplot(data)
    ax.set_xticks(range(1, len(labels) + 1))
    ax.set_xticklabels(labels, fontsize=7)
    ax.axhline(0, color="k", lw=0.8)
    ax.set_ylabel("V(selected) - V(incumbent) on replay")
    save_figure(fig, png)
    ok = all(v["regressions_with_incumbent"] == 0 for v in out.values())
    if ok and any(v["regressions_without_incumbent"] > 0 for v in out.values()):
        verdict = ("REPRODUCED: with the incumbent as a candidate V* >= V0 in every phase; without it "
                   + ", ".join(f"{k}: {v['regressions_without_incumbent']}/{v['phases']} phases regress"
                               for k, v in out.items()))
    else:
        verdict = ("PARTIAL: guarantee holds but no regressions observed without the incumbent" if ok
                   else "NOT reproduced: guarantee violated")
    for v in out.values():
        v.pop("rows")
    save("e4_safe_selection", {"config": {"rounds": rounds, "M": 4, "grid": [6, 4], "W": 4, "seeds": a.seeds,
                                          "developer": "ParametricMutator(sigma)" if a.llm == "sim" else a.llm},
                               "results": out, "figure": str(png), "verdict": verdict}, a.out)
    print(verdict)


if __name__ == "__main__":
    main()
