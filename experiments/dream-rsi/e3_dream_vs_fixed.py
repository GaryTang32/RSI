"""E3: Dream-RSI vs Recursive Fixed Exploration at equal agent-call budgets (plus hindsight-tuned fixed grids).

    python experiments/dream-rsi/e3_dream_vs_fixed.py [--llm sim|claude:haiku] [--seeds N] [--quick]
        [--domains synthetic,sumdiff,circlepack,lasso,autocorr] [--objective eq1|pareto] [--no-controls] [--reverdict]
"""
import json
import time
from pathlib import Path

import numpy as np
from _common import (RESULTS, agent_of, best_at, calls_to, developer_of, domain_of, figure, fmt, paired, parse_args,
                     pmap, sandbox_of, save, save_figure, summ, tagged)

from rsi.core import transfer_report
from rsi.dream import Config, run

# grid (branches, refine), W = grid width (every workspace in parallel, paper §4 p.7), fixed rounds, M; the math
# tasks run 10 rounds in the paper (autocorr follows it). Both arms share the per-round budget.
SETTINGS = {
    "synthetic": dict(grid=(6, 4), W=6, rounds=8, M=6, max_rounds=40),
    "sumdiff": dict(grid=(5, 3), W=5, rounds=5, M=4, max_rounds=20),
    "circlepack": dict(grid=(5, 3), W=5, rounds=5, M=4, max_rounds=20),
    "lasso": dict(grid=(5, 3), W=5, rounds=5, M=4, max_rounds=20),
    "autocorr": dict(grid=(5, 3), W=5, rounds=10, M=4, max_rounds=40),
}
CONTROL_GRIDS = [(6, 2), (6, 1), (4, 4), (4, 2), (3, 3)]  # (branches, refinements) of the hindsight-tuned Fixed arm
NONINF = 0.05  # non-inferiority margin for "comparable": a fraction of Fixed's mean gain over the seed program
DEFAULT_DOMAINS = "synthetic,sumdiff,circlepack,lasso,autocorr"


def one(job):
    """One run; ``grid`` set = a control arm (Fixed on another fixed grid)."""
    dom_name, seed, dream, llm, quick, grid, objective = job
    st = dict(SETTINGS[dom_name])
    if quick:
        st["rounds"] = min(st["rounds"], 4)
    dom = domain_of(dom_name, seed)
    budget = st["rounds"] * st["grid"][0] * (st["grid"][1] + 1)
    g = grid or st["grid"]
    rounds = st["max_rounds"] if dream else (-(-budget // (g[0] * (g[1] + 1))) if grid else st["rounds"])
    cfg = Config(rounds=rounds, W=st["W"], branch_count=g[0], refine_count=g[1], M=st["M"], dream=dream,
                 sandbox=sandbox_of(llm), seed=seed, max_calls=budget, agent_workers=1 if llm == "sim" else st["W"],
                 objective=objective)
    res = run(dom, config=cfg, agent=agent_of(dom, llm), developer=developer_of(llm) if dream else None)
    cost = res.usage["_cost"]
    # developer requests per live round: the dreaming after round t is paid before round t + 1
    dev_per_round = [len(r["dream"].get("values", [None])) - 1 if "dream" in r else 0 for r in res.trajectory]
    out = {"domain": dom_name, "seed": seed, "arm": "dream" if dream else (f"fixed_{g[0]}x{g[1]}" if grid else "fixed"),
           "budget": budget, "W": st["W"],
           "curve": [(0, res.meta["seed_score"])] + [(r["cum_calls"], r["best"]) for r in res.trajectory],
           "calls_per_round": [r["calls"] for r in res.trajectory],
           "round_best": [r["round_best"] for r in res.trajectory], "rounds": len(res.trajectory),
           "final_best": res.meta["best_score"], "seed_score": res.meta["seed_score"],
           "replay_episodes": cost["replay_episodes"], "replay_cpu_s": cost["replay_cpu_s"],
           "developer_calls": cost["developer_calls"], "developer_revisions": cost["developer_revisions"],
           "developer_requests_per_round": dev_per_round, "llm_calls_total": cost["llm_calls_total"],
           "round_budget": cfg.round_cap, "truncated_rounds": [w.meta.get("truncated_batch", {}).get("round")
                                                                for w in res.meta["worlds"]],
           "plans": [r["plan"] for r in res.trajectory], "betas": [r["beta"] for r in res.trajectory]}
    if dom_name == "lasso":
        rep = transfer_report(dom, None, {"seed": res.baseline, "final": res.best}, splits=("holdout",), workers=1)
        ev = dom.evaluate_split(res.best, "holdout")
        out["holdout"] = {"score": rep["splits"]["holdout"]["final"]["S"],
                          "runtime_ms": ev.diagnostics.get("runtime_ms"), "score_same_measurement": ev.score,
                          "seed_score": rep["splits"]["holdout"]["seed"]["S"]}
    return out


def gmean(xs):
    return float(np.exp(np.mean(np.log(np.asarray(xs, dtype=float)))))


def lasso_rows(rows, n_meas: int = 3):
    """Per-instance held-out table: seed, sklearn's lasso_path (medians of ``n_meas``) and every final program."""
    from rsi.domains.discovery import LassoPathDomain

    dom = domain_of("lasso", 0)
    ref = {}
    for name, art in (("seed", dom.seed_artifact()), ("sklearn", LassoPathDomain.reference_artifact())):
        ms = [dom.evaluate_split(art, "holdout") for _ in range(n_meas)]
        bad = [m for m in ms if m.fail_class != "ok"]
        runtime = None if bad else [float(np.median([m.diagnostics["runtime_ms"][i] for m in ms]))
                                    for i in range(len(ms[0].diagnostics["runtime_ms"]))]
        ref[name] = {"runtime_ms": runtime, "gate": bad[0].error if bad else "ok"}
    fx = {r["seed"]: r["holdout"]["runtime_ms"] for r in rows if r["arm"] == "fixed"}
    dr = {r["seed"]: r["holdout"]["runtime_ms"] for r in rows if r["arm"] == "dream"}
    seeds = sorted(set(fx) & set(dr))
    n_inst = len(fx[seeds[0]])
    sk = ref["sklearn"]["runtime_ms"]
    table = []
    for i in range(n_inst):
        f_i, d_i = [fx[s][i] for s in seeds], [dr[s][i] for s in seeds]
        table.append({"instance": i, "seed_ms": ref["seed"]["runtime_ms"][i] if ref["seed"]["runtime_ms"] else None,
                      "sklearn_ms": sk[i] if sk else None, "fixed_ms_mean": float(np.mean(f_i)),
                      "dream_ms_mean": float(np.mean(d_i)),
                      "dream_faster_than_fixed": int(sum(d < f for d, f in zip(d_i, f_i))),
                      "fixed_faster_than_sklearn": int(sum(f < sk[i] for f in f_i)) if sk else None,
                      "dream_faster_than_sklearn": int(sum(d < sk[i] for d in d_i)) if sk else None})
    means = {name: {"arith_ms": summ([float(np.mean(v)) for v in per]), "geo_ms": summ([gmean(v) for v in per])}
             for name, per in (("fixed", [fx[s] for s in seeds]), ("dream", [dr[s] for s in seeds]))}
    for name in ("seed", "sklearn"):
        v = ref[name]["runtime_ms"]
        means[name] = {"arith_ms": float(np.mean(v)) if v else None, "geo_ms": gmean(v) if v else None,
                       "gate": ref[name]["gate"]}
    wins = sum(t["dream_faster_than_fixed"] for t in table)
    n_pairs = len(seeds) * n_inst
    return {"per_instance": table, "means": means, "n_pairs": n_pairs,
            "dream_vs_fixed_wins": wins, "dream_vs_fixed_losses": n_pairs - wins,
            "finals_faster_than_sklearn": {"fixed": sum(t["fixed_faster_than_sklearn"] or 0 for t in table),
                                           "dream": sum(t["dream_faster_than_sklearn"] or 0 for t in table),
                                           "of": n_pairs}}


def controls(rows):
    """Dream vs every fixed control grid and vs the best one in hindsight (same seeds)."""
    dr = {r["seed"]: r["final_best"] for r in rows if r["arm"] == "dream"}
    arms = sorted({r["arm"] for r in rows if r["arm"].startswith("fixed")})
    out = {}
    for arm in arms:
        v = {r["seed"]: r["final_best"] for r in rows if r["arm"] == arm}
        seeds = sorted(set(v) & set(dr))
        out[arm] = {"final_best": summ([v[s] for s in seeds]),
                    "dream_minus": paired([v[s] for s in seeds], [dr[s] for s in seeds]),
                    "dream_wins": int(sum(dr[s] > v[s] + 1e-12 for s in seeds)), "n": len(seeds)}
    best = max(arms, key=lambda a: out[a]["final_best"]["mean"])
    gap = out["fixed"]["dream_minus"]["mean_diff"]
    return {"arms": out, "hindsight_best_fixed_grid": best, "dream_minus_hindsight_best": out[best]["dream_minus"],
            "dream_wins_vs_hindsight_best": out[best]["dream_wins"],
            "share_of_gap_closed_by_tuning": (1.0 - out[best]["dream_minus"]["mean_diff"] / gap) if gap else None}


def analyse(rows, dom_name, rounds_eq):
    fx = {r["seed"]: r for r in rows if r["arm"] == "fixed"}
    dr = {r["seed"]: r for r in rows if r["arm"] == "dream"}
    seeds = sorted(set(fx) & set(dr))
    budget = rows[0]["budget"]
    at = {}
    for f in [0.25, 0.5, 0.75, 1.0]:
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
    R = rounds_eq
    fx_calls = [sum(fx[s]["calls_per_round"][:R]) for s in seeds]
    dr_calls = [sum(dr[s]["calls_per_round"][:R]) for s in seeds]
    fx_best = [max(fx[s]["round_best"][:R] + [fx[s]["seed_score"]]) for s in seeds]
    dr_best = [max(dr[s]["round_best"][:R] + [dr[s]["seed_score"]]) for s in seeds]
    # the whole LLM bill at equal rounds: agent calls + the developer requests of the preceding dreaming phases
    dr_dev = [sum(dr[s]["developer_requests_per_round"][:max(0, R - 1)]) for s in seeds]
    dr_llm = [a + b for a, b in zip(dr_calls, dr_dev)]
    eq = {"rounds": R, "fixed_calls": summ(fx_calls), "dream_calls": summ(dr_calls),
          "fixed_best": summ(fx_best), "dream_best": summ(dr_best), "best_diff": paired(fx_best, dr_best),
          "calls_ratio_fixed_over_dream": summ([a / b for a, b in zip(fx_calls, dr_calls) if b]),
          "dream_developer_requests": summ(dr_dev), "dream_llm_calls_incl_developer": summ(dr_llm),
          "llm_calls_ratio_fixed_over_dream_incl_developer": summ([a / b for a, b in zip(fx_calls, dr_llm) if b])}
    plans_over = sum(1 for s in seeds for p in dr[s]["plans"]
                     if p["branch_count"] * (p["refine_count"] + 1) > (dr[s]["round_budget"] or 10 ** 9))
    budget_check = {"per_round_budget": dr[seeds[0]]["round_budget"] if seeds else None,
                    "fixed_per_round_calls": max((max(fx[s]["calls_per_round"]) for s in seeds), default=None),
                    "dream_max_calls_in_a_round": max((max(dr[s]["calls_per_round"]) for s in seeds), default=None),
                    "dream_plans_larger_than_the_budget": f"{plans_over}/{sum(len(dr[s]['plans']) for s in seeds)}",
                    "dream_rounds_cut_by_the_budget": sum(1 for s in seeds for t in dr[s]["truncated_rounds"]
                                                          if t is not None)}
    return {"seeds": seeds, "budget": budget, "best_at_budget_fraction": at, "calls_to_target": ctt,
            "dream_reached_target": f"{len(reached)}/{len(seeds)}",
            "calls_to_target_ratio": summ([c["ratio"] for c in ctt if c["ratio"]]),
            "equal_rounds": eq, "dream_rounds": summ([dr[s]["rounds"] for s in seeds]),
            "per_round_budget_check": budget_check,
            "developer_requests_at_equal_budget": summ([dr[s]["developer_revisions"] for s in seeds]),
            "llm_calls_at_equal_budget": {"fixed": summ([fx[s]["llm_calls_total"] for s in seeds]),
                                          "dream": summ([dr[s]["llm_calls_total"] for s in seeds])},
            "replay_episodes": summ([dr[s]["replay_episodes"] for s in seeds]),
            "replay_cpu_s": summ([dr[s]["replay_cpu_s"] for s in seeds])}


def _full_budget(baf: dict) -> dict:
    """The equal-budget entry: a float key in memory, a str key after a JSON round trip."""
    return baf[1.0] if 1.0 in baf else baf["1.0"]


def make_verdicts(results: dict, raw: list) -> dict:
    """Verdict per domain from the analysed results (also used by ``--reverdict``)."""
    verdicts = {}
    for d, r in results.items():
        at_budget = _full_budget(r["best_at_budget_fraction"])
        full = at_budget["dream_minus_fixed"]
        eq = r["equal_rounds"]
        # the margin is relative to Fixed's mean gain over the seed, not to the raw score scale
        seed_mean = float(np.mean([x["seed_score"] for x in raw if x["domain"] == d and x["arm"] == "fixed"]))
        gain = max(1e-12, (at_budget["fixed"]["mean"] or 0.0) - seed_mean)
        lo_pct = 100.0 * full["lo"] / gain
        head = ("REPRODUCED: better at equal budget (CI above 0)" if full["lo"] > 0 else
                f"PARTIAL: comparable at equal budget (non-inferior: CI lower bound {lo_pct:+.1f}% of "
                f"Fixed's gain over the seed, margin {100 * NONINF:.0f}%)" if full["lo"] > -NONINF * gain else
                "NOT reproduced: worse at equal budget (CI below 0)" if full["hi"] < 0 else
                f"INCONCLUSIVE at equal budget: difference {full['mean_diff']:+.4g} [{full['lo']:+.4g}, "
                f"{full['hi']:+.4g}] (lower bound {lo_pct:+.1f}% of Fixed's gain; margin "
                f"{100 * NONINF:.0f}%; n={full['n']})")
        fewer = (eq["dream_calls"]["mean"] or 0) < (eq["fixed_calls"]["mean"] or 0)
        calls = (f"; fewer calls at equal rounds ({eq['dream_calls']['mean']:.0f} vs {eq['fixed_calls']['mean']:.0f})"
                 if fewer else "; not fewer calls at equal rounds")
        llm = eq.get("dream_llm_calls_incl_developer")
        dev = (f" ({eq['dream_llm_calls_incl_developer']['mean']:.0f} vs {eq['fixed_calls']['mean']:.0f} LLM calls "
               f"counting the policy developer's {eq['dream_developer_requests']['mean']:.0f} requests)"
               if llm and llm.get("mean") is not None else "")
        verdicts[d] = head + calls + dev
        if "controls" in r:
            c = r["controls"]
            dm = c["dream_minus_hindsight_best"]
            rel = ("Dream still beats it" if dm["lo"] > 0 else "it beats Dream" if dm["hi"] < 0 else "they tie")
            verdicts[d] += (
                f"; control: against the best fixed grid chosen in hindsight ({c['hindsight_best_fixed_grid']}, "
                f"optimistic for Fixed) Dream is {dm['mean_diff']:+.4f} [{dm['lo']:+.4f}, {dm['hi']:+.4f}]: "
                f"{rel}. Tuning the fixed grid closes {100 * (c['share_of_gap_closed_by_tuning'] or 0):.0f}% "
                "of the gap to the paper's fixed baseline, so the equal-budget gain comes mainly from "
                "spending fewer calls per round (which Dream learns without hindsight tuning), not from "
                "better within-round decisions")
    if "lasso" in results and "holdout" in results["lasso"]:
        verdicts["lasso"] = _lasso_verdict(verdicts["lasso"], results["lasso"], raw)
    return verdicts


def _lasso_verdict(v: str, res: dict, raw: list) -> str:
    """Qualify the search-score verdict by the held-out re-measurement (the search score is a max over noise)."""
    h = res["holdout"]
    full_l = _full_budget(res["best_at_budget_fraction"])["dream_minus_fixed"]
    fx_h, dr_h = ([r["holdout"]["score"] for r in raw if r["domain"] == "lasso" and r["arm"] == arm]
                  for arm in ("fixed", "dream"))
    hd = h["dream_minus_fixed"] = paired(fx_h, dr_h)
    ho_gain = max(1e-12, (h["fixed_score"]["mean"] or 0.0) - (h["seed_score"]["mean"] or 0.0))
    if v.startswith("REPRODUCED") and not hd["lo"] > 0:
        v = "PARTIAL: search score better at equal budget but NOT on the held-out re-measurement" + \
            v[len("REPRODUCED: better at equal budget (CI above 0)"):]
    elif v.startswith("PARTIAL: comparable") and not hd["lo"] > -NONINF * ho_gain:
        v = ("INCONCLUSIVE: non-inferior on the search score but the held-out re-measurement "
             f"cannot exclude a loss > {100 * NONINF:.0f}% of Fixed's held-out gain; search score "
             f"difference {full_l['mean_diff']:+.4g} [{full_l['lo']:+.4g}, {full_l['hi']:+.4g}]"
             f" (n={full_l['n']})" + v[v.index(")") + 1:])
    v += (f"; held-out re-measurement of the final programs (1/s): fixed {fmt(h['fixed_score'], 1)} "
          f"vs dream {fmt(h['dream_score'], 1)}, diff {hd['mean_diff']:+.1f} [{hd['lo']:+.1f}, "
          f"{hd['hi']:+.1f}] (seed program {fmt(h['seed_score'], 1)})")
    if "rows" in h:
        hr = h["rows"]
        v += (f"; rows: Dream faster than Fixed on {hr['dream_vs_fixed_wins']}/{hr['n_pairs']} "
              f"(seed, instance) pairs; final programs faster than scikit-learn's lasso_path on "
              f"{hr['finals_faster_than_sklearn']['fixed']}/{hr['n_pairs']} (Fixed) and "
              f"{hr['finals_faster_than_sklearn']['dream']}/{hr['n_pairs']} (Dream) - the paper's "
              "'beats sklearn on every dataset' does not reproduce with numpy programs")
    return v


def plot_curves(ax, d: str, rows: list, budget):
    """Mean best-so-far vs cumulative calls of both arms with a 95% band."""
    grid = np.linspace(0, budget, 60)
    for arm, col in (("fixed", "#C44E52"), ("dream", "#4C72B0")):
        ys = np.array([[best_at(r["curve"], c) for c in grid] for r in rows if r["arm"] == arm], dtype=float)
        m = np.nanmean(ys, 0)
        sd = np.nanstd(ys, 0) / np.sqrt(max(1, len(ys)))
        ax.plot(grid, m, color=col, label="Dream-RSI" if arm == "dream" else "Fixed (parallel refine)")
        ax.fill_between(grid, m - 1.96 * sd, m + 1.96 * sd, color=col, alpha=0.2)
    ax.set_title(d, fontsize=9)
    ax.set(xlabel="cumulative discovery-agent calls", ylabel="best score")
    ax.legend(fontsize=7)


def replot(raw, png):
    plt, _ = figure("e3_dream_vs_fixed")
    doms = [d for d in DEFAULT_DOMAINS.split(",") if any(r["domain"] == d for r in raw)]
    fig, axes = plt.subplots(1, len(doms), figsize=(4.2 * len(doms), 3.5), squeeze=False)
    for i, d in enumerate(doms):
        rows = [r for r in raw if r["domain"] == d]
        plot_curves(axes[0][i], d, rows, rows[0]["budget"])
    save_figure(fig, png)


def holdout_of(rows: list) -> dict:
    fxh = [r["holdout"] for r in rows if r["arm"] == "fixed"]
    drh = [r["holdout"] for r in rows if r["arm"] == "dream"]
    out = {"fixed_score": summ([h["score"] for h in fxh]), "dream_score": summ([h["score"] for h in drh]),
           "seed_score": summ([h["seed_score"] for h in fxh]),
           "per_instance_ms": {"fixed": [h["runtime_ms"] for h in fxh], "dream": [h["runtime_ms"] for h in drh]},
           "rows": lasso_rows([r for r in rows if r["arm"] in ("fixed", "dream")])}
    hr = out["rows"]
    print(f"   held-out rows: dream faster than fixed on {hr['dream_vs_fixed_wins']}/{hr['n_pairs']} "
          f"(seed, instance) pairs; finals faster than sklearn: fixed {hr['finals_faster_than_sklearn']['fixed']}"
          f"/{hr['n_pairs']}, dream {hr['finals_faster_than_sklearn']['dream']}/{hr['n_pairs']}")
    return out


def report(d: str, res: dict) -> None:
    full = res["best_at_budget_fraction"][1.0]
    print(f"[{d}] budget {res['budget']} calls, {len(res['seeds'])} seeds")
    print(f"   best at equal budget: fixed {fmt(full['fixed'])} dream {fmt(full['dream'])} "
          f"diff {full['dream_minus_fixed']['mean_diff']:+.4f} [{full['dream_minus_fixed']['lo']:+.4f}, "
          f"{full['dream_minus_fixed']['hi']:+.4f}] wins {full['wins']} losses {full['losses']}")
    print(f"   calls-to-target (Fixed's final best): dream reached {res['dream_reached_target']}, "
          f"ratio fixed/dream {fmt(res['calls_to_target_ratio'], 2)}")
    eq = res["equal_rounds"]
    print(f"   after {eq['rounds']} rounds: calls fixed {fmt(eq['fixed_calls'], 1)} dream {fmt(eq['dream_calls'], 1)};"
          f" best fixed {fmt(eq['fixed_best'])} dream {fmt(eq['dream_best'])}; dream incl. developer requests "
          f"{fmt(eq['dream_llm_calls_incl_developer'], 1)} LLM calls")
    print(f"   per-round budget: {res['per_round_budget_check']}")


def reverdict(path: Path) -> None:
    d = json.loads(path.read_text())
    d["verdict"] = make_verdicts(d["results"], d["raw"])
    d["verdict_recomputed"] = time.strftime("%Y-%m-%d %H:%M:%S")
    path.write_text(json.dumps(d, indent=1, default=str))
    for k, v in d["verdict"].items():
        print(f"verdict [{k}]: {v}")


def main():
    a = parse_args("E3 Dream-RSI vs Recursive Fixed Exploration", default_seeds=20, extra=lambda ap: (
        ap.add_argument("--domains", default=DEFAULT_DOMAINS),
        ap.add_argument("--objective", default="eq1", choices=("eq1", "pareto"),
                        help="Dream's selection objective (the Fixed arm has none)"),
        ap.add_argument("--no-controls", action="store_true", help="skip the synthetic hindsight fixed grids"),
        ap.add_argument("--reverdict", action="store_true",
                        help="recompute the verdicts of an existing result file without running anything")))
    if a.reverdict:
        return reverdict(Path(a.out or RESULTS / f"{tagged('e3_dream_vs_fixed')}.json"))
    doms = a.domains.split(",")
    n_seeds = {"synthetic": a.seeds, "sumdiff": max(2, a.seeds // 2), "circlepack": max(2, a.seeds // 4),
               "lasso": max(2, a.seeds // 5), "autocorr": max(2, a.seeds // 2)}
    if a.llm != "sim":
        n_seeds = {d: 1 for d in doms}
    results, raw = {}, []
    plt, png = figure("e3_dream_vs_fixed")
    if a.out:  # a run saved elsewhere (e.g. --objective pareto) gets its own figure next to its JSON
        png = Path(a.out).with_suffix(".png")
    fig, axes = plt.subplots(1, len(doms), figsize=(4.2 * len(doms), 3.5), squeeze=False)
    for i, d in enumerate(doms):
        jobs = [(d, s, dream, a.llm, a.quick, None, a.objective) for s in range(n_seeds[d]) for dream in (False, True)]
        if d == "synthetic" and a.llm == "sim" and not a.no_controls:
            jobs += [(d, s, False, a.llm, a.quick, g, "eq1") for s in range(n_seeds[d]) for g in CONTROL_GRIDS]
        rows = pmap(one, jobs, 1 if d == "lasso" else a.workers)
        raw.extend(rows)
        res = analyse([r for r in rows if r["arm"] in ("fixed", "dream")], d,
                      min(5, SETTINGS[d]["rounds"]) if not a.quick else 3)
        if any(r["arm"].startswith("fixed_") for r in rows):
            c = res["controls"] = controls(rows)
            dm = c["dream_minus_hindsight_best"]
            print(f"   control: hindsight-best fixed grid {c['hindsight_best_fixed_grid']}: dream minus it "
                  f"{dm['mean_diff']:+.4f} [{dm['lo']:+.4f}, {dm['hi']:+.4f}], dream wins "
                  f"{c['dream_wins_vs_hindsight_best']}/{len(res['seeds'])}; tuning closes "
                  f"{100 * (c['share_of_gap_closed_by_tuning'] or 0):.0f}% of the gap")
        if d == "lasso":
            res["holdout"] = holdout_of(rows)
        results[d] = res
        report(d, res)
        plot_curves(axes[0][i], d, rows, res["budget"])
    save_figure(fig, png)
    verdicts = make_verdicts(results, raw)
    for d, v in verdicts.items():
        print(f"verdict [{d}]: {v}")
    out_path = a.out or str(RESULTS / f"{tagged('e3_dream_vs_fixed')}.json")
    prev = json.loads(Path(out_path).read_text()) if Path(out_path).exists() and a.domains != DEFAULT_DOMAINS else None
    if prev:  # merge a partial rerun (e.g. --domains lasso) into the full result file
        results = {**prev.get("results", {}), **results}
        raw = [r for r in prev.get("raw", []) if r["domain"] not in doms] + raw
        verdicts = {**prev.get("verdict", {}), **verdicts}
        n_seeds = {**prev.get("config", {}).get("seeds", {}), **n_seeds}
    save("e3_dream_vs_fixed", {"config": {"settings": SETTINGS, "seeds": n_seeds, "llm": a.llm, "quick": a.quick,
                                          "objective": ("eq1 beta1=0.01 beta2=0.005 normalized" if a.objective == "eq1"
                                                        else "pareto: pareto.auc - 0.1 * parallel_penalty over beta "
                                                             "(0.2..1.0)"),
                                          "round_budget": "fallback: Dream's per-round calls capped at Fixed's grid",
                                          "W": "grid width (all workspaces in parallel)",
                                          "developer": "ParametricMutator" if a.llm == "sim" else a.llm},
                               "results": results, "raw": raw, "figure": str(png), "verdict": verdicts}, a.out)
    if prev:
        replot(raw, png)


if __name__ == "__main__":
    main()
