"""E3: candidate selection (Pareto vs CurrentBest, BeamSearch, EpsilonGreedy, TopKPareto) on four RuleWorld variants.

Run: python experiments/gepa/e3_selection.py [--llm sim|claude:haiku] [--seeds N] [--quick] [--replot]
"""
from __future__ import annotations

import json
import sys

import numpy as np

from _common import (RESULTS, NewestTieBest, by_seed, ci_err, fmt, paired, parse_args, pool_map, pyplot,
                     reflection_llm, save, save_fig, summarize)
from rsi.domains.ruleworld import ReflectionProfile, make_domain
from rsi.gepa import Config, run, tree_metrics

WORLDS = {"partial": ({}, {}), "binary": ({"scoring": "binary"}, {}),
          "interference": ({"n_interfering": 4}, {}),
          "interference_undiagnosable": ({"n_interfering": 4}, {"p_diagnose": 0.0})}
ARMS = {"pareto": ("pareto", "round_robin"), "current_best": ("current_best", "round_robin"),
        "beam_search4": ("beam_search", "round_robin"), "epsilon_greedy": ("epsilon_greedy", "round_robin"),
        "top_k_pareto5": ("top_k_pareto", "round_robin"), "pareto+all": ("pareto", "all"),
        "current_best+all": ("current_best", "all"),
        "current_best_newest_tie": ("current_best_newest_tie", "round_robin")}
METRICS = ("final_test", "best_val", "max_depth", "mean_branching", "n_leaves", "frac_selected_top",
           "distinct_parents", "stalled_iters", "n_candidates")


def job(spec):
    world, arm, seed, B, llm = spec
    sel, mod = ARMS[arm]
    wkw, pkw = WORLDS[world]
    d = make_domain(seed=seed, **wkw)
    propose = reflection_llm(llm, d.world, profile=ReflectionProfile(**pkw))
    custom = NewestTieBest() if sel == "current_best_newest_tie" else None
    res = run(d, d.seed_artifact(), llm_propose=propose, selector=custom,
              config=Config(max_metric_calls=B, seed=seed, module_selector=mod,
                            candidate_selection="current_best" if custom else sel))
    st = res.state
    tm = tree_metrics(st)
    return {"world": world, "arm": arm, "seed": seed, "B": B, "final_test": d.expected(res.best, "test"),
            "best_val": res.meta["best_val"], "stalled_iters": st.i - st.discovery_iter[st.best_idx()],
            "iterations": st.i + 1, "oracle": d.expected(d.oracle_artifact(), "test"), **tm}


def plot_grouped(out: dict, budgets, worlds, arms) -> None:
    fig, axes = pyplot().subplots(1, len(budgets), figsize=(6.5 * len(budgets), 4), squeeze=False)
    width = 0.8 / len(arms)
    for ax, B in zip(axes[0], budgets):
        for j, arm in enumerate(arms):
            st = [out["summary"][f"{w}@B={B}"][arm]["final_test"] for w in worlds]
            xs = [i + (j - (len(arms) - 1) / 2) * width for i in range(len(worlds))]
            ax.bar(xs, [x["mean"] for x in st], width, label=arm, yerr=ci_err(st), capsize=2)
        ax.set_xticks(range(len(worlds)))
        ax.set_xticklabels([w.replace("_", "\n") for w in worlds], fontsize=8)
        ax.set_title(f"B = {B} rollouts", fontsize=10)
        ax.set_ylabel("true test score")
        ax.grid(axis="y", alpha=0.3)
    handles, labels = axes[0][0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=len(arms), fontsize=7, frameon=False)
    fig.suptitle("E3: candidate selection strategy x RuleWorld variant (mean, 95% bootstrap CI, 30 seeds)",
                 fontsize=10)
    save_fig(fig, RESULTS / "e3_selection.png", rect=(0, 0.07, 1, 1))


def cell_verdict(key: str, s: dict, pc: dict, pn: dict, best_arm: str, cb_rows: list[dict]) -> str:
    nan = float("nan")
    return (f"[{key}] Pareto {fmt(s['pareto']['final_test'])} vs CurrentBest {fmt(s['current_best']['final_test'])}"
            + (f", BeamSearch(4) {fmt(s['beam_search4']['final_test'])}" if "beam_search4" in s else "")
            + f"; Pareto - CurrentBest = {pc.get('mean_diff', nan):+.3f} "
              f"[{pc.get('lo', nan):+.3f}, {pc.get('hi', nan):+.3f}]"
            + (f"; Pareto - CurrentBest(newest tie) = {pn['mean_diff']:+.3f} [{pn['lo']:+.3f}, {pn['hi']:+.3f}]"
               if pn else "")
            + f"; best arm: {best_arm}. "
            f"Tree: Pareto depth {s['pareto']['max_depth']['mean']:.1f}, leaves {s['pareto']['n_leaves']['mean']:.1f}, "
            f"expands current best {s['pareto']['frac_selected_top']['mean']:.0%} vs CurrentBest depth "
            f"{s['current_best']['max_depth']['mean']:.1f}, leaves {s['current_best']['n_leaves']['mean']:.1f}, "
            f"stalled for the last {s['current_best']['stalled_iters']['mean']:.0f} of "
            f"{np.mean([r['iterations'] for r in cb_rows]):.0f} iterations.")


def main():
    if "--replot" in sys.argv:
        o = json.loads((RESULTS / "e3_selection.json").read_text())
        wl = [w for w in WORLDS if any(k.startswith(w + "@") for k in o["summary"])]
        return plot_grouped(o, o["config"]["budgets"], wl, list(next(iter(o["summary"].values()))))
    a = parse_args("E3: candidate selection strategies", default_seeds=30)
    budgets = [400] if a.live else ([1500] if a.quick else [1500, 4000])
    arms = list(ARMS) if not a.live else ["pareto", "current_best", "current_best_newest_tie"]
    worlds = list(WORLDS) if not a.live else ["binary"]
    jobs = [(w, arm, s, B, a.llm) for B in budgets for w in worlds for arm in arms for s in range(a.seeds)]
    rows = pool_map(job, jobs, a.workers)
    out = {"experiment": "E3 candidate selection", "llm": a.llm, "config": {"budgets": budgets, "seeds": a.seeds,
                                                                            "worlds": WORLDS, "arms": ARMS},
           "summary": {}, "paired_vs_pareto": {}, "raw": rows}
    verdicts = []
    for B in budgets:
        for w in worlds:
            key = f"{w}@B={B}"
            by = {arm: by_seed(rows, world=w, B=B, arm=arm) for arm in arms}
            s = out["summary"][key] = {arm: {m: summarize([r[m] for r in rs]) for m in METRICS}
                                       for arm, rs in by.items()}
            pv = out["paired_vs_pareto"][key] = {
                arm: paired([r["final_test"] for r in by[arm]], [r["final_test"] for r in by["pareto"]])
                for arm in arms if arm != "pareto"}
            best_arm = max(arms, key=lambda k: s[k]["final_test"]["mean"])
            verdicts.append(cell_verdict(key, s, pv.get("current_best", {}), pv.get("current_best_newest_tie", {}),
                                         best_arm, by["current_best"]))
    pv = out["paired_vs_pareto"]
    wins = {k: v.get("current_best", {}).get("lo", -1) > 0 for k, v in pv.items()}
    losses = {k: v.get("current_best", {}).get("hi", 1) < 0 for k, v in pv.items()}
    repro = sorted({k.split("@")[0] for k, v in wins.items() if v})
    notrep = sorted({k.split("@")[0] for k in wins} - set(repro))
    tie = {k: v.get("current_best_newest_tie", {}) for k, v in pv.items()}
    tie_wins = [k for k, v in tie.items() if v.get("lo", -1) > 0]
    out["tie_control"] = {"pareto_beats_newest_tie_best": tie_wins,
                          "pareto_loses_to_newest_tie_best": [k for k, v in tie.items() if v.get("hi", 1) < 0]}
    out["verdict"] = (" ".join(verdicts) + f" Pareto significantly beats CurrentBest in "
                      f"{[k for k, v in wins.items() if v]}; "
                      f"significantly loses in {[k for k, v in losses.items() if v]}. "
                      f"Claim 'Pareto selection beats greedy (SelectBestCandidate)': REPRODUCED in worlds {repro}; "
                      f"NOT reproduced in {notrep}. Control: Pareto significantly beats a CurrentBest that breaks "
                      f"ties toward the newest candidate in {tie_wins}; where it does not, the gain over the "
                      f"reference CurrentBest is a tie-breaking effect on a validation plateau, not frontier "
                      f"diversity.")
    save("e3_selection", out, a.out)
    plot_grouped(out, budgets, worlds, arms)
    print(out["verdict"])


if __name__ == "__main__":
    main()
