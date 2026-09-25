"""E3 - Main claim: dreaming improves the quality / discovery-cost trade-off.

Claim [paper:§4, Fig.3-4; doc "317 vs 550 agent calls"]: with the same agent, evaluator,
initialization and per-round cap, Dream-RSI reaches comparable or better quality with
fewer discovery-agent calls than Recursive Fixed Exploration (parallel refine kept fixed).

Both arms get the same total budget of agent calls (``--rounds`` x the fixed per-round
cap). Fixed runs its grid every round; Dream-RSI dreams after every live search and may
spend less per round (so it can run more rounds). Reported per domain, over seeds:
best vs cumulative calls, best at equal budget, calls-to-target (target = Fixed's final
best), the paper-style equal-rounds view (calls and best after R rounds), and - for
Lasso - the held-out per-instance runtimes of the final programs.

    python experiments/dream-rsi/e3_dream_vs_fixed.py [--llm sim|claude:haiku] [--seeds N] [--quick]
        [--domains synthetic,sumdiff,circlepack,lasso]
"""
import numpy as np
import json
from pathlib import Path

from _common import (RESULTS, agent_of, best_at, calls_to, developer_of, domain_of, figure, fmt, paired, parse_args,
                     pmap, save, summ)

from rsi.core import transfer_report
from rsi.dream import Config, run

SETTINGS = {  # grid (branches, refine), W, fixed rounds, M, seeds divisor
    "synthetic": dict(grid=(6, 4), W=4, rounds=8, M=6, max_rounds=40),
    "sumdiff": dict(grid=(5, 3), W=4, rounds=5, M=4, max_rounds=20),
    "circlepack": dict(grid=(5, 3), W=4, rounds=5, M=4, max_rounds=20),
    "lasso": dict(grid=(5, 3), W=4, rounds=5, M=4, max_rounds=20),
}


def one(job):
    dom_name, seed, dream, llm, quick = job
    st = dict(SETTINGS[dom_name])
    if quick:
        st["rounds"] = min(st["rounds"], 4)
    dom = domain_of(dom_name, seed)
    per_round = st["grid"][0] * (st["grid"][1] + 1)
    budget = st["rounds"] * per_round
    cfg = Config(rounds=st["max_rounds"] if dream else st["rounds"], W=st["W"], branch_count=st["grid"][0],
                 refine_count=st["grid"][1], M=st["M"], dream=dream, sandbox="inprocess", seed=seed,
                 max_calls=budget, agent_workers=1 if llm == "sim" else st["W"])
    res = run(dom, config=cfg, agent=agent_of(dom, llm), developer=developer_of(llm) if dream else None)
    curve = [(0, res.meta["seed_score"])] + [(r["cum_calls"], r["best"]) for r in res.trajectory]
    out = {"domain": dom_name, "seed": seed, "arm": "dream" if dream else "fixed", "budget": budget,
           "curve": curve, "calls_per_round": [r["calls"] for r in res.trajectory],
           "round_best": [r["round_best"] for r in res.trajectory], "rounds": len(res.trajectory),
           "final_best": res.meta["best_score"], "seed_score": res.meta["seed_score"],
           "replay_episodes": res.usage["_cost"]["replay_episodes"], "replay_cpu_s": res.usage["_cost"]["replay_cpu_s"],
           "developer_calls": res.usage["_cost"]["developer_calls"],
           "plans": [r["plan"] for r in res.trajectory], "betas": [r["beta"] for r in res.trajectory]}
    if dom_name == "lasso":
        rep = transfer_report(dom, None, {"seed": res.baseline, "final": res.best}, splits=("holdout",), workers=1)
        ho = rep["splits"]["holdout"]["final"]
        ev = dom.evaluate_split(res.best, "holdout")
        out["holdout"] = {"score": ho["S"], "runtime_ms": ev.diagnostics.get("runtime_ms"),
                          "seed_score": rep["splits"]["holdout"]["seed"]["S"]}
    return out


def analyse(rows, dom_name, rounds_eq):
    fx = {r["seed"]: r for r in rows if r["arm"] == "fixed"}
    dr = {r["seed"]: r for r in rows if r["arm"] == "dream"}
    seeds = sorted(set(fx) & set(dr))
    budget = rows[0]["budget"]
    fracs = [0.25, 0.5, 0.75, 1.0]
    at = {}
    for f in fracs:
        c = f * budget
        a = [best_at(fx[s]["curve"], c) for s in seeds]
        b = [best_at(dr[s]["curve"], c) for s in seeds]
        at[f] = {"calls": c, "fixed": summ(a), "dream": summ(b), "dream_minus_fixed": paired(a, b),
                 "wins": int(sum(y > x + 1e-12 for x, y in zip(a, b))),
                 "losses": int(sum(y < x - 1e-12 for x, y in zip(a, b)))}
    ctt = []
    for s in seeds:
        target = fx[s]["final_best"]
        cf, cd = calls_to(fx[s]["curve"], target), calls_to(dr[s]["curve"], target)
        ctt.append({"seed": s, "target": target, "fixed_calls": cf, "dream_calls": cd,
                    "ratio": (cf / cd) if (cf and cd) else None})
    reached = [c for c in ctt if c["dream_calls"] is not None]
    eq = {}
    R = rounds_eq
    fx_calls = [sum(fx[s]["calls_per_round"][:R]) for s in seeds]
    dr_calls = [sum(dr[s]["calls_per_round"][:R]) for s in seeds]
    fx_best = [max(fx[s]["round_best"][:R] + [fx[s]["seed_score"]]) for s in seeds]
    dr_best = [max(dr[s]["round_best"][:R] + [dr[s]["seed_score"]]) for s in seeds]
    eq = {"rounds": R, "fixed_calls": summ(fx_calls), "dream_calls": summ(dr_calls),
          "fixed_best": summ(fx_best), "dream_best": summ(dr_best), "best_diff": paired(fx_best, dr_best),
          "calls_ratio_fixed_over_dream": summ([a / b for a, b in zip(fx_calls, dr_calls) if b])}
    return {"seeds": seeds, "budget": budget, "best_at_budget_fraction": at, "calls_to_target": ctt,
            "dream_reached_target": f"{len(reached)}/{len(seeds)}",
            "calls_to_target_ratio": summ([c["ratio"] for c in ctt if c["ratio"]]),
            "equal_rounds": eq, "dream_rounds": summ([dr[s]["rounds"] for s in seeds]),
            "replay_episodes": summ([dr[s]["replay_episodes"] for s in seeds]),
            "replay_cpu_s": summ([dr[s]["replay_cpu_s"] for s in seeds])}


DEFAULT_DOMAINS = "synthetic,sumdiff,circlepack,lasso"


def replot(raw, png):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    doms = [d for d in DEFAULT_DOMAINS.split(",") if any(r["domain"] == d for r in raw)]
    fig, axes = plt.subplots(1, len(doms), figsize=(4.2 * len(doms), 3.5), squeeze=False)
    for i, d in enumerate(doms):
        rows = [r for r in raw if r["domain"] == d]
        ax = axes[0][i]
        grid = np.linspace(0, rows[0]["budget"], 60)
        for arm, col in (("fixed", "#C44E52"), ("dream", "#4C72B0")):
            ys = np.array([[best_at([tuple(p) for p in r["curve"]], c) for c in grid] for r in rows if r["arm"] == arm],
                          dtype=float)
            m = np.nanmean(ys, 0)
            sd = np.nanstd(ys, 0) / np.sqrt(max(1, len(ys)))
            ax.plot(grid, m, color=col, label="Dream-RSI" if arm == "dream" else "Fixed (parallel refine)")
            ax.fill_between(grid, m - 1.96 * sd, m + 1.96 * sd, color=col, alpha=0.2)
        ax.set_title(d, fontsize=9)
        ax.set_xlabel("cumulative discovery-agent calls")
        ax.set_ylabel("best score")
        ax.legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(png, dpi=130)


def main():
    a = parse_args("E3 Dream-RSI vs Recursive Fixed Exploration", default_seeds=20, extra=lambda ap: ap.add_argument(
        "--domains", default=DEFAULT_DOMAINS))
    doms = a.domains.split(",")
    n_seeds = {"synthetic": a.seeds, "sumdiff": max(2, a.seeds // 2), "circlepack": max(2, a.seeds // 4),
               "lasso": max(2, a.seeds // 5)}
    if a.llm != "sim":
        n_seeds = {d: 1 for d in doms}
    results, raw = {}, []
    plt, png = figure("e3_dream_vs_fixed")
    fig, axes = plt.subplots(1, len(doms), figsize=(4.2 * len(doms), 3.5), squeeze=False)
    for i, d in enumerate(doms):
        jobs = [(d, s, dream, a.llm, a.quick) for s in range(n_seeds[d]) for dream in (False, True)]
        rows = pmap(one, jobs, 1 if d == "lasso" else a.workers)
        raw.extend(rows)
        R = min(5, SETTINGS[d]["rounds"]) if not a.quick else 3
        res = analyse(rows, d, R)
        if d == "lasso":
            fxh = [r["holdout"] for r in rows if r["arm"] == "fixed"]
            drh = [r["holdout"] for r in rows if r["arm"] == "dream"]
            res["holdout"] = {"fixed_score": summ([h["score"] for h in fxh]), "dream_score": summ([h["score"] for h in drh]),
                              "seed_score": summ([h["seed_score"] for h in fxh]),
                              "per_instance_ms": {"fixed": [h["runtime_ms"] for h in fxh],
                                                  "dream": [h["runtime_ms"] for h in drh]}}
        results[d] = res
        full = res["best_at_budget_fraction"][1.0]
        print(f"[{d}] budget {res['budget']} calls, {len(res['seeds'])} seeds")
        print(f"   best at equal budget: fixed {fmt(full['fixed'])} dream {fmt(full['dream'])} "
              f"diff {full['dream_minus_fixed']['mean_diff']:+.4f} [{full['dream_minus_fixed']['lo']:+.4f}, "
              f"{full['dream_minus_fixed']['hi']:+.4f}] wins {full['wins']} losses {full['losses']}")
        print(f"   calls-to-target (Fixed's final best): dream reached {res['dream_reached_target']}, "
              f"ratio fixed/dream {fmt(res['calls_to_target_ratio'], 2)}")
        eq = res["equal_rounds"]
        print(f"   after {eq['rounds']} rounds: calls fixed {fmt(eq['fixed_calls'], 1)} dream {fmt(eq['dream_calls'], 1)};"
              f" best fixed {fmt(eq['fixed_best'])} dream {fmt(eq['dream_best'])}")
        ax = axes[0][i]
        grid = np.linspace(0, res["budget"], 60)
        for arm, col in (("fixed", "#C44E52"), ("dream", "#4C72B0")):
            ys = np.array([[best_at(r["curve"], c) for c in grid] for r in rows if r["arm"] == arm], dtype=float)
            m = np.nanmean(ys, 0)
            sd = np.nanstd(ys, 0) / np.sqrt(max(1, len(ys)))
            ax.plot(grid, m, color=col, label="Dream-RSI" if arm == "dream" else "Fixed (parallel refine)")
            ax.fill_between(grid, m - 1.96 * sd, m + 1.96 * sd, color=col, alpha=0.2)
        ax.set_title(d, fontsize=9)
        ax.set_xlabel("cumulative discovery-agent calls")
        ax.set_ylabel("best score")
        ax.legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(png, dpi=130)
    verdicts = {}
    for d, r in results.items():
        full = r["best_at_budget_fraction"][1.0]["dream_minus_fixed"]
        eq = r["equal_rounds"]
        better = full["lo"] > 0
        comparable = full["lo"] > -0.01 * max(1e-9, abs(r["best_at_budget_fraction"][1.0]["fixed"]["mean"] or 1))
        fewer = (eq["dream_calls"]["mean"] or 0) < (eq["fixed_calls"]["mean"] or 0)
        verdicts[d] = ("REPRODUCED: better at equal budget (CI above 0)" if better else
                       "PARTIAL: comparable at equal budget" if comparable else "NOT reproduced at equal budget") + \
            (f"; fewer calls at equal rounds ({eq['dream_calls']['mean']:.0f} vs {eq['fixed_calls']['mean']:.0f})"
             if fewer else "; not fewer calls at equal rounds")
    if "lasso" in results and "holdout" in results["lasso"]:
        h = results["lasso"]["holdout"]
        fx_h = [r["holdout"]["score"] for r in raw if r["domain"] == "lasso" and r["arm"] == "fixed"]
        dr_h = [r["holdout"]["score"] for r in raw if r["domain"] == "lasso" and r["arm"] == "dream"]
        h["dream_minus_fixed"] = paired(fx_h, dr_h)
        verdicts["lasso"] += (f"; held-out re-measurement of the final programs (1/s): fixed {fmt(h['fixed_score'], 1)} "
                              f"vs dream {fmt(h['dream_score'], 1)} (seed program {fmt(h['seed_score'], 1)}); the search "
                              "score is a max over noisy runtime measurements, so the held-out re-measurement is the "
                              "fairer quality comparison")
    for d, v in verdicts.items():
        print(f"verdict [{d}]: {v}")
    out_path = a.out or str(RESULTS / "e3_dream_vs_fixed.json")
    prev = json.loads(Path(out_path).read_text()) if Path(out_path).exists() and a.domains != DEFAULT_DOMAINS else None
    if prev:  # merge a partial rerun (e.g. --domains lasso) into the full result file
        results = {**prev.get("results", {}), **results}
        raw = [r for r in prev.get("raw", []) if r["domain"] not in doms] + raw
        verdicts = {**prev.get("verdict", {}), **verdicts}
        n_seeds = {**prev.get("config", {}).get("seeds", {}), **n_seeds}
    save("e3_dream_vs_fixed", {"config": {"settings": SETTINGS, "seeds": n_seeds, "llm": a.llm, "quick": a.quick,
                                          "objective": "eq1 beta1=0.01 beta2=0.005 normalized",
                                          "developer": "ParametricMutator" if a.llm == "sim" else a.llm},
                               "results": results, "raw": raw, "figure": str(png), "verdict": verdicts}, a.out)
    if prev:
        replot(raw, png)


if __name__ == "__main__":
    main()
