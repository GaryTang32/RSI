"""E11: Dream vs Recursive Fixed Exploration at the paper's grids and equal round counts; preregistered tests T1-T7.

    python experiments/dream-rsi/e11_paper_grid.py [--domains synthetic,lasso,...] [--seeds-scale 1.0] [--reanalyse]
"""
import json
import time
from pathlib import Path

import numpy as np
from _common import RESULTS, domain_of, figure, paired, parse_args, pmap, save, save_figure, summ

from rsi.dream import Config, DreamRSILoop, ParametricMutator
from rsi.dream.question import program_only

# per-round grids: Pro 10 workspaces x 11 calls = 110 (1 root + 10 refinements), Flash 32 x 20 = 640; W = the grid's
# width; rounds 5 (Lasso, Fig. 3b) / 10 (math) [paper §4 p.7, §4.1, §4.2]
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
    """The best-so-far (score, program) after every round, with the loop's tie rule (higher score, earliest node)."""
    best, art, out = loop.seed_eval.score, program_only(loop.seed_artifact), []
    for w in worlds:
        imp = [n for n in w.non_root() if n.success and n.score is not None and n.score > best]
        if imp:
            bn = max(imp, key=lambda n: (n.score, -n.seq))
            best, art = float(bn.score), program_only(loop.store.get(bn.artifact_id))
        out.append((best, art))
    return out


def config_of(name, seed, dream):
    """The run's Config; the hard caps must not be below the grid, since the loop clips every plan to them."""
    st = SETTINGS[name]
    b, r = st["grid"]
    return Config(rounds=st["rounds"], W=st["W"], branch_count=b, refine_count=r, M=st["M"], dream=dream,
                  sandbox="inprocess", seed=seed, agent_workers=1, trace=False,
                  hard_max_branch=max(12, b), hard_max_refine=max(12, r))


def one(job):
    name, seed, dream = job
    dom = domain_of("synthetic" if name == "synthetic_flash" else name, seed)
    cfg = config_of(name, seed, dream)
    t0 = time.time()
    loop = DreamRSILoop(dom.as_task(), dom.mock_agent(), config=cfg, developer=ParametricMutator() if dream else None)
    res = loop.run()
    tr = res.trajectory
    row = {"domain": name, "seed": seed, "arm": "dream" if dream else "fixed", "seed_score": res.meta["seed_score"],
           "calls": [x["calls"] for x in tr], "cum_calls": [x["cum_calls"] for x in tr],
           "best": [x["best"] for x in tr],
           "round_best": [x["round_best"] for x in tr], "betas": [x["beta"] for x in tr],
           "plans": [[x["plan"]["branch_count"], x["plan"]["refine_count"]] for x in tr],
           "developer_revisions": res.usage["_cost"]["developer_revisions"],
           "replay_episodes": res.usage["_cost"]["replay_episodes"], "wall_s": round(time.time() - t0, 1)}
    if name == "lasso":  # re-measure the best-so-far program on the held-out split after every round
        row["holdout_seed"] = dom.evaluate_split(program_only(loop.seed_artifact), "holdout").score
        row["holdout"], row["holdout_ms"] = [], []
        cache: dict = {}
        for _, art in best_path(loop, res.meta["worlds"]):
            if art.id not in cache:
                ev = dom.evaluate_split(art, "holdout")
                cache[art.id] = (ev.score if ev.fail_class == "ok" else 0.0, ev.diagnostics.get("runtime_ms"))
            row["holdout"].append(cache[art.id][0])
            row["holdout_ms"].append(cache[art.id][1])
    return row


def _by(rows, dom, arm):
    return {r["seed"]: r for r in rows if r["domain"] == dom and r["arm"] == arm}


def _arms(rows, dom):
    fx, dr = _by(rows, dom, "fixed"), _by(rows, dom, "dream")
    return fx, dr, sorted(set(fx) & set(dr))


def interp_fixed(fx_row, key, calls):
    xs = [0] + fx_row["cum_calls"]
    ys = [fx_row["holdout_seed"] if key == "holdout" else fx_row["seed_score"]] + fx_row[key]
    return float(np.interp(calls, xs, ys))


def per_round_tests(rows, dom, key):
    """T1 (equal rounds) and T2 (Fixed interpolated at Dream's cumulative calls) at rounds 2..R."""
    fx, dr, seeds = _arms(rows, dom)
    R = min(min(len(fx[s][key]) for s in seeds), min(len(dr[s][key]) for s in seeds))
    t1, t2 = [], []
    for r in range(1, R):
        b = [dr[s][key][r] for s in seeds]
        t1.append({"round": r + 1, **paired([fx[s][key][r] for s in seeds], b)})
        c = [interp_fixed(fx[s], key, dr[s]["cum_calls"][r]) for s in seeds]
        t2.append({"round": r + 1, "dream_cum_calls": summ([dr[s]["cum_calls"][r] for s in seeds]), **paired(c, b)})
    return {"T1_equal_rounds": t1, "T1_consistently_superior": all(x["lo"] > 0 for x in t1),
            "T2_compute_axis": t2, "T2_consistently_superior": all(x["lo"] > 0 for x in t2),
            "round1_identical": all(abs(fx[s][key][0] - dr[s][key][0]) < 1e-12 for s in seeds) if key != "holdout"
            else None, "n": len(seeds)}


def _per_round(dom):
    return SETTINGS[dom]["grid"][0] * (SETTINGS[dom]["grid"][1] + 1)


def call_ratio(rows, dom):
    fx, dr, seeds = _arms(rows, dom)
    rat = [fx[s]["cum_calls"][-1] / dr[s]["cum_calls"][-1] for s in seeds]
    out = {"fixed_spends_its_grid_every_round": all(c == _per_round(dom) for s in seeds for c in fx[s]["calls"]),
           "ratio_fixed_over_dream": summ(rat), "fixed_calls": summ([fx[s]["cum_calls"][-1] for s in seeds]),
           "dream_calls": summ([dr[s]["cum_calls"][-1] for s in seeds]),
           "dream_over_fixed": summ([1 / x for x in rat]),
           "dream_llm_calls_incl_developer": summ([dr[s]["cum_calls"][-1] + dr[s]["developer_revisions"]
                                                   for s in seeds])}
    s = out["ratio_fixed_over_dream"]
    if dom in PAPER_RATIO:
        out["paper_ratio"] = PAPER_RATIO[dom]
        out["ci_contains_paper_ratio"] = bool(s["lo"] <= PAPER_RATIO[dom] <= s["hi"])
    out["substantially_lower"] = bool(s["lo"] > 1.25)
    return out


def final_diff(rows, dom, key="best"):
    fx, dr, seeds = _arms(rows, dom)
    a = [fx[s][key][-1] for s in seeds]
    b = [dr[s][key][-1] for s in seeds]
    base = "holdout_seed" if key == "holdout" else "seed_score"
    gain = float(np.mean(a) - np.mean([fx[s][base] for s in seeds]))
    return {**paired(a, b), "fixed_gain_over_seed": gain, "margin": NONINF * max(gain, 1e-12),
            "fixed": summ(a), "dream": summ(b)}


def pacing(rows, dom):
    dr = [r for r in rows if r["domain"] == dom and r["arm"] == "dream"]
    early = summ([float(np.mean(r["calls"][1:4])) for r in dr])
    clustered = []
    for r in dr:
        b = [r["seed_score"]] + r["best"]
        imp = np.diff(b)
        scale = max(1e-12, (b[-1] - b[0]) / len(imp))
        pl, im = [], []
        for t in range(2, len(r["calls"])):
            (pl if imp[t - 1] / scale < 0.1 else im).append(r["calls"][t] - r["calls"][t - 1])
        if pl and im:
            clustered.append(float(np.mean(pl) - np.mean(im)))
    cl = summ(clustered)
    return {"calls_rounds_2_4": early, "fixed_calls_per_round": _per_round(dom),
            "conserves": bool(early["hi"] is not None and early["hi"] < _per_round(dom)),
            "delta_calls_plateau_minus_improving_seed_clustered": cl,
            "re_expands": bool(cl["lo"] is not None and cl["lo"] > 0),
            "effort_by_round": [summ([r["calls"][t] for r in dr if len(r["calls"]) > t])["mean"]
                                for t in range(max(len(r["calls"]) for r in dr))],
            "strict_improvement_rounds": summ([float(np.mean(np.diff([r["seed_score"]] + r["best"]) > 1e-12))
                                               for r in dr])}


def analyse(rows):
    out = {}
    for d in (d for d in SETTINGS if any(r["domain"] == d for r in rows)):
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
    tests = {"T1_T2_Q15": {d: {"T1": out[d]["holdout" if d == "lasso" else "search"]["T1_consistently_superior"],
                               "T2": out[d]["holdout" if d == "lasso" else "search"]["T2_consistently_superior"]}
                           for d in ("lasso", "synthetic", "synthetic_flash") if d in out},
             "T3_Q10_Q11": {d: out[d]["calls"] for d in ("synthetic", "lasso", "synthetic_flash") if d in out}}
    if all(d in out for d in MATH):
        sd, cp, ac = out["sumdiff"]["final_best"], out["circlepack"]["final_best"], out["autocorr"]["final_best"]
        t4 = {"sumdiff_better": bool(sd["lo"] > 0),
              "circlepack_tie": bool(-cp["margin"] < cp["lo"] and cp["hi"] < cp["margin"]),
              "autocorr_slightly_worse": bool(ac["mean_diff"] <= 0 and ac["lo"] > -ac["margin"])}
        t4["all_three"] = all(t4.values())
        tests["T4_Q20"] = t4
    t5 = {}
    for d in (d for d in REAL if d in out):
        q = out[d]["final_holdout" if d == "lasso" else "final_best"]
        t5[d] = {"fewer_calls_20pct": bool(out[d]["calls"]["dream_over_fixed"]["hi"] < 0.8),
                 "non_inferior": bool(q["lo"] > -q["margin"])}
        t5[d]["both"] = t5[d]["fewer_calls_20pct"] and t5[d]["non_inferior"]
    tests["T5_L4"] = {"per_domain": t5, "domains_passing": sum(v["both"] for v in t5.values()),
                      "holds": sum(v["both"] for v in t5.values()) >= 2}
    t6 = {d: {"conserves": out[d]["pacing"]["conserves"], "re_expands": out[d]["pacing"]["re_expands"]}
          for d in ("synthetic", "sumdiff", "autocorr") if d in out}
    tests["T6_L7"] = {"per_domain": t6, "holds": sum(v["conserves"] and v["re_expands"] for v in t6.values()) >= 2}
    sh = {d: out[d]["default_beta_moved_share"] for d in out if "default_beta_moved_share" in out[d]}
    tests["T7_L10"] = {"per_domain": sh,
                       "pooled_share": float(np.mean([len(set(r["betas"])) > 1 for r in rows if r["arm"] == "dream"
                                                      and r["domain"] in SETTINGS
                                                      and SETTINGS[r["domain"]]["rounds"] >= 10])) if sh else None}
    if sh:
        tests["T7_L10"]["holds"] = tests["T7_L10"]["pooled_share"] >= 0.5
    return out, tests


def plot(plt, rows, png):
    doms = [d for d in SETTINGS if any(r["domain"] == d for r in rows)]
    fig, axes = plt.subplots(1, len(doms), figsize=(4.0 * len(doms), 3.4), squeeze=False)
    for ax, d in zip(axes[0], doms):
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
    save_figure(fig, png)


def _save(rows, path):
    out, tests = analyse(rows)
    plt, png = figure("e11_paper_grid")
    if path != RESULTS / "e11_paper_grid.json":
        png = Path(path).with_suffix(".png")
    plot(plt, rows, png)
    save("e11_paper_grid", {"config": {"settings": SETTINGS, "objective": "eq1 beta1=0.01 beta2=0.005 normalized",
                                       "round_budget": "fallback (110 or 640 = Fixed's per-round calls)",
                                       "comparison": "equal rounds (the paper's protocol)", "developer":
                                       "ParametricMutator", "agent": "domain mock agents"},
                            "results": out, "tests": tests, "raw": rows, "figure": str(png)}, str(path))
    return tests


def main():
    a = parse_args("E11 paper-protocol grid", default_seeds=0, extra=lambda ap: (
        ap.add_argument("--domains", default=",".join(SETTINGS)),
        ap.add_argument("--seeds-scale", type=float, default=1.0, help="fraction of the preregistered seeds"),
        ap.add_argument("--seed-offset", type=int, default=0,
                        help="smoke tests only: seeds outside the preregistered range"),
        ap.add_argument("--reanalyse", action="store_true")))
    path = Path(a.out or RESULTS / "e11_paper_grid.json")
    rows = list((json.loads(path.read_text()) if path.exists() else {}).get("raw", []))
    if not a.reanalyse:
        for d in a.domains.split(","):
            n = max(2, int(round(SETTINGS[d]["seeds"] * a.seeds_scale)))
            t0 = time.time()
            new = pmap(one, [(d, a.seed_offset + s, dream) for s in range(n) for dream in (False, True)], a.workers)
            rows = [r for r in rows if r["domain"] != d] + new
            print(f"[{d}] {len(new)} runs in {time.time() - t0:.0f}s", flush=True)
            _save(rows, path)  # after every domain: a crash later loses nothing
    print(json.dumps(_save(rows, path), indent=1, default=str))


if __name__ == "__main__":
    main()
