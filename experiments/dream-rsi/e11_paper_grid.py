"""E11 - Dream vs Recursive Fixed Exploration at the paper's grid and round counts (claims audit, retry round 2).

E3 compared the arms on small grids (5-6 workspaces x 4-5 calls) and mostly at equal *budget*. The paper's
protocol is different [paper:§4 p.7, §4.1, §4.2]: 10 parallel workspaces x up to 11 calls = 110 agent calls per
round for Gemini-3.1-Pro (32 x 20 = 640 for Flash), identical per-round budgets, and the same number of
ROUNDS for both arms (5 for Lasso, 10 for the math tasks); Fig. 3b plots the held-out runtime of each round's
program against cumulative agent calls. This script runs that protocol at CPU scale (mock agents) and tests
the preregistered hypotheses T1-T7 of claims-audit §6:

* T1 (Q15, equal rounds): Dream - Fixed at every round r = 2..R (Lasso: held-out score of the best-so-far
  program, re-measured after every round; search score secondary);
* T2 (Q15, compute axis): Dream at its round ends vs Fixed linearly interpolated at the same cumulative calls;
* T3 (Q10/Q11): Fixed/Dream cumulative-call ratio after R rounds (paper: 550/317 = 1.735, 3200/1879 = 1.703);
* T4 (Q20): math at R = 10: sum-diff better, circle packing a tie, autocorr slightly worse;
* T5 (L4): >= 2 real tasks with >= 20% fewer calls and non-inferior quality at equal rounds;
* T6 (L7): pacing - fewer calls in rounds 2-4 and re-expansion after plateaus;
* T7 (L10): the cross-cycle default-beta rule moves the default in >= 50% of 10-round runs.

    python experiments/dream-rsi/e11_paper_grid.py [--domains synthetic,lasso,...] [--seeds-scale 1.0] [--workers 2]
"""
import json
import time
from pathlib import Path

import numpy as np

from _common import RESULTS, domain_of, figure, paired, parse_args, pmap, save, summ

from rsi.dream import Config, DreamRSILoop, ParametricMutator
from rsi.dream.question import program_only

#: the paper's per-round grids: Pro 10 workspaces x 11 calls = 110 (grid (10, 10): 1 root + 10 refinements),
#: Flash 32 x 20 = 640; W = the grid's width (all workspaces in parallel); rounds 5 (Lasso, Fig. 3b) / 10 (math)
SETTINGS = {
    "synthetic": dict(grid=(10, 10), W=10, rounds=5, M=6, seeds=40),
    "synthetic_flash": dict(grid=(32, 19), W=32, rounds=5, M=6, seeds=20),
    "lasso": dict(grid=(10, 10), W=10, rounds=5, M=4, seeds=8),
    "sumdiff": dict(grid=(10, 10), W=10, rounds=10, M=4, seeds=20),
    "autocorr": dict(grid=(10, 10), W=10, rounds=10, M=4, seeds=20),
    "circlepack": dict(grid=(10, 10), W=10, rounds=10, M=4, seeds=8),
}
MATH = ("sumdiff", "circlepack", "autocorr")
REAL = ("sumdiff", "circlepack", "autocorr", "lasso")
NONINF = 0.05
PAPER_RATIO = {"synthetic": 550 / 317, "lasso": 550 / 317, "synthetic_flash": 3200 / 1879}


def best_path(loop, worlds):
    """The loop's best-so-far program after every round (same tie rule as the loop: higher score, then the
    earliest node) as (score, artifact) pairs."""
    best, art, out = loop.seed_eval.score, program_only(loop.seed_artifact), []
    for w in worlds:
        imp = [n for n in w.non_root() if n.success and n.score is not None and n.score > best]
        if imp:
            bn = max(imp, key=lambda n: (n.score, -n.seq))
            best, art = float(bn.score), program_only(loop.store.get(bn.artifact_id))
        out.append((best, art))
    return out


def one(job):
    name, seed, dream = job
    st = SETTINGS[name]
    dom = domain_of("synthetic" if name == "synthetic_flash" else name, seed)
    b, r = st["grid"]
    cfg = Config(rounds=st["rounds"], W=st["W"], branch_count=b, refine_count=r, M=st["M"], dream=dream,
                 sandbox="inprocess", seed=seed, agent_workers=1, trace=False)
    t0 = time.time()
    loop = DreamRSILoop(dom.as_task(), dom.mock_agent(), config=cfg, developer=ParametricMutator() if dream else None)
    res = loop.run()
    tr = res.trajectory
    row = {"domain": name, "seed": seed, "arm": "dream" if dream else "fixed", "seed_score": res.meta["seed_score"],
           "calls": [x["calls"] for x in tr], "cum_calls": [x["cum_calls"] for x in tr], "best": [x["best"] for x in tr],
           "round_best": [x["round_best"] for x in tr], "betas": [x["beta"] for x in tr],
           "plans": [[x["plan"]["branch_count"], x["plan"]["refine_count"]] for x in tr],
           "developer_revisions": res.usage["_cost"]["developer_revisions"],
           "replay_episodes": res.usage["_cost"]["replay_episodes"], "wall_s": round(time.time() - t0, 1)}
    if name == "lasso":
        path = best_path(loop, res.meta["worlds"])
        seed_ev = dom.evaluate_split(program_only(loop.seed_artifact), "holdout")
        row["holdout_seed"] = seed_ev.score
        row["holdout"], row["holdout_ms"] = [], []
        cache: dict = {}
        for s, art in path:                      # re-measure the best-so-far program after every round
            if art.id not in cache:
                ev = dom.evaluate_split(art, "holdout")
                cache[art.id] = (ev.score if ev.fail_class == "ok" else 0.0, ev.diagnostics.get("runtime_ms"))
            row["holdout"].append(cache[art.id][0])
            row["holdout_ms"].append(cache[art.id][1])
    return row


# ------------------------------------------------------------------------------------ analysis
def _by(rows, dom, arm):
    return {r["seed"]: r for r in rows if r["domain"] == dom and r["arm"] == arm}


def interp_fixed(fx_row, key, calls):
    xs = [0] + fx_row["cum_calls"]
    ys = [fx_row["holdout_seed"] if key == "holdout" else fx_row["seed_score"]] + fx_row[key]
    return float(np.interp(calls, xs, ys))


def per_round_tests(rows, dom, key):
    fx, dr = _by(rows, dom, "fixed"), _by(rows, dom, "dream")
    seeds = sorted(set(fx) & set(dr))
    R = min(min(len(fx[s][key]) for s in seeds), min(len(dr[s][key]) for s in seeds))
    t1, t2 = [], []
    for r in range(1, R):                           # rounds 2..R (0-based index r)
        a = [fx[s][key][r] for s in seeds]
        b = [dr[s][key][r] for s in seeds]
        t1.append({"round": r + 1, **paired(a, b)})
        c = [interp_fixed(fx[s], key, dr[s]["cum_calls"][r]) for s in seeds]
        t2.append({"round": r + 1, "dream_cum_calls": summ([dr[s]["cum_calls"][r] for s in seeds]), **paired(c, b)})
    return {"T1_equal_rounds": t1, "T1_consistently_superior": all(x["lo"] > 0 for x in t1),
            "T2_compute_axis": t2, "T2_consistently_superior": all(x["lo"] > 0 for x in t2),
            "round1_identical": all(abs(fx[s][key][0] - dr[s][key][0]) < 1e-12 for s in seeds) if key != "holdout"
            else None, "n": len(seeds)}


def call_ratio(rows, dom):
    fx, dr = _by(rows, dom, "fixed"), _by(rows, dom, "dream")
    seeds = sorted(set(fx) & set(dr))
    rat = [fx[s]["cum_calls"][-1] / dr[s]["cum_calls"][-1] for s in seeds]
    out = {"ratio_fixed_over_dream": summ(rat), "fixed_calls": summ([fx[s]["cum_calls"][-1] for s in seeds]),
           "dream_calls": summ([dr[s]["cum_calls"][-1] for s in seeds]),
           "dream_over_fixed": summ([1 / x for x in rat]),
           "dream_llm_calls_incl_developer": summ([dr[s]["cum_calls"][-1] + dr[s]["developer_revisions"] for s in seeds])}
    s = out["ratio_fixed_over_dream"]
    if dom in PAPER_RATIO:
        out["paper_ratio"] = PAPER_RATIO[dom]
        out["ci_contains_paper_ratio"] = bool(s["lo"] <= PAPER_RATIO[dom] <= s["hi"])
    out["substantially_lower"] = bool(s["lo"] > 1.25)
    return out


def final_diff(rows, dom, key="best"):
    fx, dr = _by(rows, dom, "fixed"), _by(rows, dom, "dream")
    seeds = sorted(set(fx) & set(dr))
    a = [fx[s][key][-1] for s in seeds]
    b = [dr[s][key][-1] for s in seeds]
    base = "holdout_seed" if key == "holdout" else "seed_score"
    gain = float(np.mean(a) - np.mean([fx[s][base] for s in seeds]))
    d = paired(a, b)
    return {**d, "fixed_gain_over_seed": gain, "margin": NONINF * max(gain, 1e-12),
            "fixed": summ(a), "dream": summ(b)}


def pacing(rows, dom):
    dr = [r for r in rows if r["domain"] == dom and r["arm"] == "dream"]
    per = SETTINGS[dom]["grid"][0] * (SETTINGS[dom]["grid"][1] + 1)
    early = summ([float(np.mean(r["calls"][1:4])) for r in dr])
    clustered = []
    for r in dr:
        b = [r["seed_score"]] + r["best"]
        imp = np.diff(b)
        scale = max(1e-12, (b[-1] - b[0]) / len(imp))
        pl, im = [], []
        for t in range(2, len(r["calls"])):
            d = r["calls"][t] - r["calls"][t - 1]
            (pl if imp[t - 1] / scale < 0.1 else im).append(d)
        if pl and im:
            clustered.append(float(np.mean(pl) - np.mean(im)))
    cl = summ(clustered)
    return {"calls_rounds_2_4": early, "fixed_calls_per_round": per,
            "conserves": bool(early["hi"] is not None and early["hi"] < per),
            "delta_calls_plateau_minus_improving_seed_clustered": cl,
            "re_expands": bool(cl["lo"] is not None and cl["lo"] > 0),
            "effort_by_round": [summ([r["calls"][t] for r in dr if len(r["calls"]) > t])["mean"]
                                for t in range(max(len(r["calls"]) for r in dr))],
            "strict_improvement_rounds": summ([float(np.mean(np.diff([r["seed_score"]] + r["best"]) > 1e-12))
                                               for r in dr])}


def analyse(rows):
    doms = [d for d in SETTINGS if any(r["domain"] == d for r in rows)]
    out = {}
    for d in doms:
        res = {"search": per_round_tests(rows, d, "best"), "calls": call_ratio(rows, d),
               "final_best": final_diff(rows, d)}
        if d == "lasso":
            res["holdout"] = per_round_tests(rows, d, "holdout")
            res["final_holdout"] = final_diff(rows, d, "holdout")
        if d in ("synthetic", "sumdiff", "autocorr"):
            res["pacing"] = pacing(rows, d)
        if SETTINGS[d]["rounds"] >= 10:
            dr = [r for r in rows if r["domain"] == d and r["arm"] == "dream"]
            res["default_beta_moved_share"] = float(np.mean([len(set(r["betas"])) > 1 for r in dr]))
        out[d] = res
    tests = {}
    q15 = {d: {"T1": out[d]["holdout" if d == "lasso" else "search"]["T1_consistently_superior"],
               "T2": out[d]["holdout" if d == "lasso" else "search"]["T2_consistently_superior"]}
           for d in ("lasso", "synthetic", "synthetic_flash") if d in out}
    tests["T1_T2_Q15"] = q15
    tests["T3_Q10_Q11"] = {d: out[d]["calls"] for d in ("synthetic", "lasso", "synthetic_flash") if d in out}
    if all(d in out for d in MATH):
        sd, cp, ac = out["sumdiff"]["final_best"], out["circlepack"]["final_best"], out["autocorr"]["final_best"]
        t4 = {"sumdiff_better": bool(sd["lo"] > 0),
              "circlepack_tie": bool(-cp["margin"] < cp["lo"] and cp["hi"] < cp["margin"]),
              "autocorr_slightly_worse": bool(ac["mean_diff"] <= 0 and ac["lo"] > -ac["margin"])}
        t4["all_three"] = all(t4.values())
        tests["T4_Q20"] = t4
    t5 = {}
    for d in REAL:
        if d not in out:
            continue
        q = out[d]["final_holdout" if d == "lasso" else "final_best"]
        rat = out[d]["calls"]["dream_over_fixed"]
        t5[d] = {"fewer_calls_20pct": bool(rat["hi"] < 0.8), "non_inferior": bool(q["lo"] > -q["margin"])}
        t5[d]["both"] = t5[d]["fewer_calls_20pct"] and t5[d]["non_inferior"]
    tests["T5_L4"] = {"per_domain": t5, "domains_passing": sum(v["both"] for v in t5.values()),
                      "holds": sum(v["both"] for v in t5.values()) >= 2}
    t6 = {d: {"conserves": out[d]["pacing"]["conserves"], "re_expands": out[d]["pacing"]["re_expands"]}
          for d in ("synthetic", "sumdiff", "autocorr") if d in out}
    tests["T6_L7"] = {"per_domain": t6, "holds": sum(v["conserves"] and v["re_expands"] for v in t6.values()) >= 2}
    sh = {d: out[d]["default_beta_moved_share"] for d in out if "default_beta_moved_share" in out[d]}
    tests["T7_L10"] = {"per_domain": sh,
                       "pooled_share": float(np.mean([len(set(r["betas"])) > 1 for r in rows if r["arm"] == "dream"
                                                      and SETTINGS[r["domain"]]["rounds"] >= 10])) if sh else None}
    if sh:
        tests["T7_L10"]["holds"] = tests["T7_L10"]["pooled_share"] >= 0.5
    return out, tests


def plot(rows, png):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    doms = [d for d in SETTINGS if any(r["domain"] == d for r in rows)]
    fig, axes = plt.subplots(1, len(doms), figsize=(4.0 * len(doms), 3.4), squeeze=False)
    for i, d in enumerate(doms):
        ax = axes[0][i]
        key = "holdout" if d == "lasso" else "best"
        for arm, col in (("fixed", "#C44E52"), ("dream", "#4C72B0")):
            rs = [r for r in rows if r["domain"] == d and r["arm"] == arm]
            R = min(len(r[key]) for r in rs)
            x = np.mean([r["cum_calls"][:R] for r in rs], 0)
            y = np.mean([r[key][:R] for r in rs], 0)
            ax.plot(x, y, "o-", color=col, label="Dream-RSI" if arm == "dream" else "Fixed")
            for k, (a, b) in enumerate(zip(x, y)):
                ax.annotate(str(k + 1), (a, b), fontsize=6, xytext=(2, 2), textcoords="offset points")
        ax.set_title(f"{d} ({'held-out' if d == 'lasso' else 'search'})", fontsize=8)
        ax.set_xlabel("cumulative agent calls")
        ax.legend(fontsize=6)
    fig.tight_layout()
    fig.savefig(png, dpi=130)


def main():
    a = parse_args("E11 paper-protocol grid", default_seeds=0, extra=lambda ap: (
        ap.add_argument("--domains", default=",".join(SETTINGS)),
        ap.add_argument("--seeds-scale", type=float, default=1.0, help="fraction of the preregistered seeds"),
        ap.add_argument("--seed-offset", type=int, default=0, help="smoke tests only: seeds outside the preregistered range"),
        ap.add_argument("--reanalyse", action="store_true")))
    path = Path(a.out or RESULTS / "e11_paper_grid.json")
    prev = json.loads(path.read_text()) if path.exists() else None
    rows = [r for r in (prev or {}).get("raw", [])]
    if not a.reanalyse:
        doms = a.domains.split(",")
        for d in doms:
            n = max(2, int(round(SETTINGS[d]["seeds"] * a.seeds_scale)))
            t0 = time.time()
            new = pmap(one, [(d, a.seed_offset + s, dream) for s in range(n) for dream in (False, True)], a.workers)
            rows = [r for r in rows if r["domain"] != d] + new
            print(f"[{d}] {len(new)} runs in {time.time() - t0:.0f}s", flush=True)
            tests = _save(rows, path)           # after every domain: a crash later loses nothing
    tests = _save(rows, path)
    print(json.dumps(tests, indent=1, default=str))


def _save(rows, path):
    out, tests = analyse(rows)
    plt, png = figure("e11_paper_grid")
    if path != RESULTS / "e11_paper_grid.json":
        png = Path(path).with_suffix(".png")
    plot(rows, png)
    save("e11_paper_grid", {"config": {"settings": SETTINGS, "objective": "eq1 beta1=0.01 beta2=0.005 normalized",
                                       "round_budget": "fallback (110 or 640 = Fixed's per-round calls)",
                                       "comparison": "equal rounds (the paper's protocol)", "developer":
                                       "ParametricMutator", "agent": "domain mock agents"},
                            "results": out, "tests": tests, "raw": rows, "figure": str(png)}, str(path))
    return tests


if __name__ == "__main__":
    main()
