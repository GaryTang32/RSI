"""E6 - Learned pacing: effort per round adapts to progress.

Claim [paper:§5.2 Fig.6; doc "cut attempts per round from 110 to about 50 ... then spent
more again once progress stalled"]: as performance improves the policy conserves
discovery compute; when progress plateaus it increases exploration effort again.

Dream-RSI and Fixed run R live searches (no budget cap). For every round t >= 3 we pair
the previous round's improvement of the best score with this round's agent calls, and
report (i) the Spearman correlation across all (seed, round) pairs (the claim predicts
< 0: small improvement -> more effort next), (ii) mean effort after a plateau vs after
an improving round, (iii) the per-round effort curve, plus the plan / default-beta
trajectory that produces it.

    python experiments/dream-rsi/e6_pacing.py [--llm sim|claude:haiku] [--seeds N] [--quick]
"""
import numpy as np
from _common import agent_of, developer_of, domain_of, figure, fmt, parse_args, pmap, save, spearman, summ

from rsi.dream import Config, run

SET = {"synthetic": dict(grid=(6, 4), rounds=12, M=6), "sumdiff": dict(grid=(5, 3), rounds=10, M=4)}


def one(job):
    d, seed, dream, llm, quick = job
    st = SET[d]
    dom = domain_of(d, seed)
    cfg = Config(rounds=6 if quick else st["rounds"], W=4, branch_count=st["grid"][0], refine_count=st["grid"][1],
                 M=st["M"], dream=dream, sandbox="inprocess", seed=seed, agent_workers=1 if llm == "sim" else 4)
    res = run(dom, config=cfg, agent=agent_of(dom, llm), developer=developer_of(llm) if dream else None)
    tr = res.trajectory
    return {"domain": d, "seed": seed, "arm": "dream" if dream else "fixed", "calls": [r["calls"] for r in tr],
            "best": [r["best"] for r in tr], "seed_score": res.meta["seed_score"],
            "plans": [[r["plan"]["branch_count"], r["plan"]["refine_count"]] for r in tr],
            "betas": [r["beta"] for r in tr]}


def pairs(rows):
    out = []
    for r in rows:
        b = [r["seed_score"]] + r["best"]
        imp = np.diff(b)                       # improvement in round t (index t-1)
        scale = max(1e-12, (b[-1] - b[0]) / len(imp))
        for t in range(2, len(r["calls"])):     # effort in round t+1 (0-based t) vs improvement in round t
            out.append({"seed": r["seed"], "round": t + 1, "prev_improvement": float(imp[t - 1]),
                        "prev_rel": float(imp[t - 1] / scale), "calls": r["calls"][t],
                        "delta_calls": r["calls"][t] - r["calls"][t - 1]})
    return out


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
        dr = [r for r in rows if r["arm"] == "dream"]
        fx = [r for r in rows if r["arm"] == "fixed"]
        ps = pairs(dr)
        rho = spearman([p["prev_improvement"] for p in ps], [p["calls"] for p in ps])
        plateau = [p["calls"] for p in ps if p["prev_rel"] < 0.1]
        improving = [p["calls"] for p in ps if p["prev_rel"] >= 0.1]
        per_seed = []
        for r in dr:
            pp = [p for p in ps if p["seed"] == r["seed"]]
            a_ = [p["calls"] for p in pp if p["prev_rel"] < 0.1]
            b_ = [p["calls"] for p in pp if p["prev_rel"] >= 0.1]
            if a_ and b_:
                per_seed.append(np.mean(a_) - np.mean(b_))
        d_pl = [p["delta_calls"] for p in ps if p["prev_rel"] < 0.1]
        d_im = [p["delta_calls"] for p in ps if p["prev_rel"] >= 0.1]
        rho_delta = spearman([p["prev_improvement"] for p in ps], [p["delta_calls"] for p in ps])
        R = len(dr[0]["calls"])
        effort = [summ([r["calls"][t] for r in dr if len(r["calls"]) > t]) for t in range(R)]
        res = {"spearman_prev_improvement_vs_calls": rho, "n_pairs": len(ps),
               "calls_after_plateau": summ(plateau), "calls_after_improving_round": summ(improving),
               "per_seed_plateau_minus_improving": summ(per_seed), "dream_effort_by_round": effort,
               "delta_calls_after_plateau": summ(d_pl), "delta_calls_after_improving_round": summ(d_im),
               "spearman_prev_improvement_vs_delta_calls": rho_delta,
               "fixed_effort_per_round": fx[0]["calls"][0],
               "dream_total_calls": summ([sum(r["calls"]) for r in dr]),
               "fixed_total_calls": summ([sum(r["calls"]) for r in fx]),
               "dream_final_best": summ([r["best"][-1] for r in dr]), "fixed_final_best": summ([r["best"][-1] for r in fx]),
               "example": {"calls": dr[0]["calls"], "best": dr[0]["best"], "plans": dr[0]["plans"],
                           "betas": dr[0]["betas"]}, "rows": rows}
        out[d] = res
        print(f"[{d}] Spearman(prev improvement, next calls) = {rho:+.3f} over {len(ps)} (seed, round) pairs")
        print(f"   calls after plateau {fmt(res['calls_after_plateau'], 1)} vs after improving round "
              f"{fmt(res['calls_after_improving_round'], 1)}; per-seed diff {fmt(res['per_seed_plateau_minus_improving'], 1)}")
        print(f"   round-controlled: change in calls after plateau {fmt(res['delta_calls_after_plateau'], 2)} vs after "
              f"improving round {fmt(res['delta_calls_after_improving_round'], 2)} (rho {rho_delta:+.3f})")
        print("   dream effort by round: " + " ".join(f"{e['mean']:.0f}" for e in effort) +
              f"  (fixed: {fx[0]['calls'][0]} every round)")
        ax = axes[0][i]
        ax.plot(range(1, R + 1), [e["mean"] for e in effort], "o-", color="#4C72B0", label="Dream-RSI calls/round")
        ax.axhline(fx[0]["calls"][0], color="#C44E52", ls="--", label="Fixed calls/round")
        ax2 = ax.twinx()
        ax2.plot(range(1, R + 1), np.mean([r["best"] for r in dr], 0), color="#55A868", label="Dream best")
        ax.set_xlabel("live search (round)")
        ax.set_ylabel("agent calls in round")
        ax2.set_ylabel("best so far (mean)")
        ax.set_title(d, fontsize=9)
        ax.legend(fontsize=7, loc="lower left")
    fig.tight_layout()
    fig.savefig(png, dpi=130)
    verdict = {}
    for d, r in out.items():
        rho = r["spearman_prev_improvement_vs_calls"]
        diff = r["per_seed_plateau_minus_improving"]
        dp, di = r["delta_calls_after_plateau"], r["delta_calls_after_improving_round"]
        conserve = r["dream_effort_by_round"][1]["mean"] < r["fixed_effort_per_round"]
        rises = dp["mean"] is not None and di["mean"] is not None and dp["mean"] > di["mean"]
        if rho < -0.1 and diff["mean"] is not None and diff["lo"] > 0 and rises:
            verdict[d] = (f"REPRODUCED: effort rises after plateaus (rho={rho:+.2f}, +{diff['mean']:.1f} calls; "
                          f"round-controlled change {dp['mean']:+.1f} vs {di['mean']:+.1f})")
        elif rises or rho < 0:
            verdict[d] = (f"WEAK/PARTIAL: rho={rho:+.2f}; change in calls after plateau {dp['mean']:+.1f} vs after "
                          f"improvement {di['mean']:+.1f}")
        else:
            verdict[d] = (f"NOT reproduced: effort does not rise after plateaus (rho={rho:+.2f}; change after plateau "
                          f"{dp['mean']:+.1f} vs {di['mean']:+.1f} after improvement)")
        verdict[d] += ("; conserves compute after round 1 (" + f"{r['dream_effort_by_round'][1]['mean']:.0f} vs "
                       f"{r['fixed_effort_per_round']} calls)" if conserve else "")
        print(f"verdict [{d}]: {verdict[d]}")
    for r in out.values():
        r.pop("rows")
    save("e6_pacing", {"config": {"settings": SET, "seeds": n_seeds, "plateau_rule": "prev improvement < 10% of the "
                                  "run's mean per-round improvement", "llm": a.llm},
                       "results": out, "figure": str(png), "verdict": verdict}, a.out)


if __name__ == "__main__":
    main()
