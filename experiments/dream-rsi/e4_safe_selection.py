"""E4 - Safe selection: the current policy is always a candidate.

Claim [paper:§3 p.6; doc "Deploy the best version, including the current one as a
candidate"]: selecting argmax over versions *including the incumbent* guarantees
V^{m*} >= V^0 on replay over H_t. Ablation: select among the revisions only.

Synthetic worlds, Dream loops with M = 4 (3 revisions per phase), two developer
noise levels. Per dreaming phase we log V^{m*} - V^0 (replay, same worlds) and the
next live search's online value (raw Eq.1 of that round: gain over its root - beta1 N
+ beta2 N/k); per run the final best at equal budget.

    python experiments/dream-rsi/e4_safe_selection.py [--seeds N] [--quick]
"""
import numpy as np
from _common import developer_of, figure, fmt, paired, parse_args, pmap, sandbox_of, save, summ

from rsi.dream import Config, run
from rsi.domains.discovery import SyntheticConfig, SyntheticDomain


def one(job):
    seed, incl, sigma, rounds, llm = job
    dom = SyntheticDomain(SyntheticConfig(seed=seed))
    cfg = Config(rounds=rounds, W=4, branch_count=6, refine_count=4, M=4, include_incumbent=incl, sandbox=sandbox_of(llm),
                 seed=seed, agent_workers=1)
    dev = developer_of(llm, sigma=sigma) if llm == "sim" else developer_of(llm)
    res = run(dom, config=cfg, developer=dev)
    deltas, online = [], []
    tr = res.trajectory
    for i, r in enumerate(tr):
        if "dream" in r:
            deltas.append(r["dream"]["delta_vs_incumbent"])
            if i + 1 < len(tr):
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
        jobs = [(s, incl, sigma, rounds, a.llm) for s in range(a.seeds) for incl in (True, False)]
        rows = pmap(one, jobs, a.workers)
        inc = [r for r in rows if r["include_incumbent"]]
        exc = [r for r in rows if not r["include_incumbent"]]
        d_inc = [d for r in inc for d in r["deltas"]]
        d_exc = [d for r in exc for d in r["deltas"]]
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
            "final_best_diff_with_minus_without": paired([r["final_best"] for r in sorted(exc, key=lambda r: r["seed"])],
                                                         [r["final_best"] for r in sorted(inc, key=lambda r: r["seed"])]),
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
    fig.tight_layout()
    fig.savefig(png, dpi=130)
    ok = all(v["regressions_with_incumbent"] == 0 for v in out.values())
    some = any(v["regressions_without_incumbent"] > 0 for v in out.values())
    verdict = ("REPRODUCED: with the incumbent as a candidate V* >= V0 in every phase; without it "
               + ", ".join(f"{k}: {v['regressions_without_incumbent']}/{v['phases']} phases regress" for k, v in out.items())
               ) if ok and some else ("PARTIAL: guarantee holds but no regressions observed without the incumbent"
                                      if ok else "NOT reproduced: guarantee violated")
    for v in out.values():
        v.pop("rows")
    save("e4_safe_selection", {"config": {"rounds": rounds, "M": 4, "grid": [6, 4], "W": 4, "seeds": a.seeds,
                                          "developer": "ParametricMutator(sigma)" if a.llm == "sim" else a.llm},
                               "results": out, "figure": str(png), "verdict": verdict}, a.out)
    print(verdict)


if __name__ == "__main__":
    main()
