"""E6: learned pacing; does Dream-RSI spend more agent calls in the round after a plateau than after progress?

    python experiments/dream-rsi/e6_pacing.py [--llm sim|claude:haiku] [--seeds N] [--quick] [--domains ...]
"""
import numpy as np
from _common import (agent_of, developer_of, domain_of, figure, fmt, parse_args, pmap, sandbox_of, save, save_figure,
                     spearman, summ)

from rsi.dream import Config, run

# W = the grid's width: every workspace of pi_1 runs in parallel
SET = {"synthetic": dict(grid=(6, 4), W=6, rounds=12, M=6), "sumdiff": dict(grid=(5, 3), W=5, rounds=10, M=4)}


def one(job):
    d, seed, dream, llm, quick = job
    st = SET[d]
    dom = domain_of(d, seed)
    cfg = Config(rounds=6 if quick else st["rounds"], W=st["W"], branch_count=st["grid"][0],
                 refine_count=st["grid"][1], M=st["M"], dream=dream, sandbox=sandbox_of(llm), seed=seed,
                 agent_workers=1 if llm == "sim" else st["W"])
    res = run(dom, config=cfg, agent=agent_of(dom, llm), developer=developer_of(llm) if dream else None)
    tr = res.trajectory
    return {"domain": d, "seed": seed, "arm": "dream" if dream else "fixed", "calls": [r["calls"] for r in tr],
            "best": [r["best"] for r in tr], "seed_score": res.meta["seed_score"],
            "plans": [[r["plan"]["branch_count"], r["plan"]["refine_count"]] for r in tr],
            "betas": [r["beta"] for r in tr]}


def pairs(rows):
    """(improvement in round t, effort in round t + 1) for t >= 2, with the improvement relative to the run's mean."""
    out = []
    for r in rows:
        b = [r["seed_score"]] + r["best"]
        imp = np.diff(b)
        scale = max(1e-12, (b[-1] - b[0]) / len(imp))
        for t in range(2, len(r["calls"])):
            out.append({"seed": r["seed"], "round": t + 1, "prev_improvement": float(imp[t - 1]),
                        "prev_rel": float(imp[t - 1] / scale), "calls": r["calls"][t],
                        "delta_calls": r["calls"][t] - r["calls"][t - 1]})
    return out


def per_seed_diff(dr, ps, key):
    """Seed-clustered mean ``key`` after a plateau minus after an improving round (pooled-pair CIs are too narrow)."""
    out = []
    for r in dr:
        pp = [p for p in ps if p["seed"] == r["seed"]]
        a_ = [p[key] for p in pp if p["prev_rel"] < 0.1]
        b_ = [p[key] for p in pp if p["prev_rel"] >= 0.1]
        if a_ and b_:
            out.append(np.mean(a_) - np.mean(b_))
    return out


def analyse(rows) -> dict:
    dr = [r for r in rows if r["arm"] == "dream"]
    fx = [r for r in rows if r["arm"] == "fixed"]
    ps = pairs(dr)
    after = {k: ([p[k] for p in ps if p["prev_rel"] < 0.1], [p[k] for p in ps if p["prev_rel"] >= 0.1])
             for k in ("calls", "delta_calls")}
    R = len(dr[0]["calls"])
    return {"spearman_prev_improvement_vs_calls": spearman([p["prev_improvement"] for p in ps],
                                                           [p["calls"] for p in ps]),
            "n_pairs": len(ps), "calls_after_plateau": summ(after["calls"][0]),
            "calls_after_improving_round": summ(after["calls"][1]),
            "per_seed_plateau_minus_improving": summ(per_seed_diff(dr, ps, "calls")),
            "dream_effort_by_round": [summ([r["calls"][t] for r in dr if len(r["calls"]) > t]) for t in range(R)],
            "delta_calls_after_plateau": summ(after["delta_calls"][0]),
            "delta_calls_after_improving_round": summ(after["delta_calls"][1]),
            "per_seed_delta_plateau_minus_improving": summ(per_seed_diff(dr, ps, "delta_calls")),
            "spearman_prev_improvement_vs_delta_calls": spearman([p["prev_improvement"] for p in ps],
                                                                 [p["delta_calls"] for p in ps]),
            "fixed_effort_per_round": fx[0]["calls"][0],
            "dream_total_calls": summ([sum(r["calls"]) for r in dr]),
            "fixed_total_calls": summ([sum(r["calls"]) for r in fx]),
            "dream_final_best": summ([r["best"][-1] for r in dr]),
            "fixed_final_best": summ([r["best"][-1] for r in fx]),
            "example": {"calls": dr[0]["calls"], "best": dr[0]["best"], "plans": dr[0]["plans"],
                        "betas": dr[0]["betas"]}, "rows": rows}


def verdict_of(r: dict) -> str:
    rho = r["spearman_prev_improvement_vs_calls"]
    diff = r["per_seed_plateau_minus_improving"]
    dp, di = r["delta_calls_after_plateau"], r["delta_calls_after_improving_round"]
    rises = dp["mean"] is not None and di["mean"] is not None and dp["mean"] > di["mean"]
    cl = r["per_seed_delta_plateau_minus_improving"]
    clustered = (f"; seed-clustered round-controlled difference {cl['mean']:+.1f} [{cl['lo']:+.1f}, {cl['hi']:+.1f}] "
                 f"(n={cl['n']} seeds)") if cl["mean"] is not None else "; no seed has both kinds of round"
    if rho < -0.1 and diff["mean"] is not None and diff["lo"] > 0 and rises and cl["mean"] is not None \
            and cl["lo"] > 0:
        v = (f"REPRODUCED: effort rises after plateaus (rho={rho:+.2f}, +{diff['mean']:.1f} calls; "
             f"round-controlled change {dp['mean']:+.1f} vs {di['mean']:+.1f})") + clustered
    elif rises or rho < 0:
        v = (f"WEAK/PARTIAL: rho={rho:+.2f}; change in calls after plateau {dp['mean']:+.1f} vs after "
             f"improvement {di['mean']:+.1f}") + clustered
    else:
        v = (f"NOT reproduced: effort does not rise after plateaus (rho={rho:+.2f}; change after plateau "
             f"{dp['mean']:+.1f} vs {di['mean']:+.1f} after improvement)")
    if r["dream_effort_by_round"][1]["mean"] < r["fixed_effort_per_round"]:
        v += ("; conserves compute after round 1 (" + f"{r['dream_effort_by_round'][1]['mean']:.0f} vs "
              f"{r['fixed_effort_per_round']} calls)")
    return v


def main():
    a = parse_args("E6 learned pacing", default_seeds=20, extra=lambda ap: ap.add_argument(
        "--domains", default="synthetic,sumdiff"))
    doms = a.domains.split(",")
    n_seeds = {"synthetic": a.seeds, "sumdiff": max(2, a.seeds // 2)}
    if a.llm != "sim":
        n_seeds = {d: 1 for d in doms}
    out = {}
    plt, png = figure("e6_pacing")
    fig, axes = plt.subplots(1, len(doms), figsize=(4.8 * len(doms), 3.5), squeeze=False)
    for i, d in enumerate(doms):
        rows = pmap(one, [(d, s, dr, a.llm, a.quick) for s in range(n_seeds[d]) for dr in (False, True)], a.workers)
        res = out[d] = analyse(rows)
        dr = [r for r in rows if r["arm"] == "dream"]
        effort, fixed_calls = res["dream_effort_by_round"], res["fixed_effort_per_round"]
        print(f"[{d}] Spearman(prev improvement, next calls) = {res['spearman_prev_improvement_vs_calls']:+.3f} over "
              f"{res['n_pairs']} (seed, round) pairs")
        print(f"   calls after plateau {fmt(res['calls_after_plateau'], 1)} vs after improving round "
              f"{fmt(res['calls_after_improving_round'], 1)}; per-seed diff "
              f"{fmt(res['per_seed_plateau_minus_improving'], 1)}")
        print(f"   round-controlled: change in calls after plateau {fmt(res['delta_calls_after_plateau'], 2)} vs after "
              f"improving round {fmt(res['delta_calls_after_improving_round'], 2)} "
              f"(rho {res['spearman_prev_improvement_vs_delta_calls']:+.3f}); "
              f"seed-clustered difference {fmt(res['per_seed_delta_plateau_minus_improving'], 2)}")
        print("   dream effort by round: " + " ".join(f"{e['mean']:.0f}" for e in effort) +
              f"  (fixed: {fixed_calls} every round)")
        ax = axes[0][i]
        R = len(effort)
        ax.plot(range(1, R + 1), [e["mean"] for e in effort], "o-", color="#4C72B0", label="Dream-RSI calls/round")
        ax.axhline(fixed_calls, color="#C44E52", ls="--", label="Fixed calls/round")
        ax2 = ax.twinx()
        ax2.plot(range(1, R + 1), np.mean([r["best"] for r in dr], 0), color="#55A868", label="Dream best")
        ax.set(xlabel="live search (round)", ylabel="agent calls in round")
        ax2.set_ylabel("best so far (mean)")
        ax.set_title(d, fontsize=9)
        ax.legend(fontsize=7, loc="lower left")
    save_figure(fig, png)
    verdict = {}
    for d, r in out.items():
        verdict[d] = verdict_of(r)
        print(f"verdict [{d}]: {verdict[d]}")
    for r in out.values():
        r.pop("rows")
    save("e6_pacing", {"config": {"settings": SET, "seeds": n_seeds, "plateau_rule": "prev improvement < 10% of the "
                                  "run's mean per-round improvement", "llm": a.llm},
                       "results": out, "figure": str(png), "verdict": verdict}, a.out)


if __name__ == "__main__":
    main()
