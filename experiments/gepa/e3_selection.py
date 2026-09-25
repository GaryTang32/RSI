"""E3 - Candidate selection: Pareto vs CurrentBest vs BeamSearch(4) vs EpsilonGreedy vs TopKPareto.

Claim [paper Table 3, Fig.4][doc "keep every candidate best on at least one example"]:
Pareto selection escapes local optima that greedy selection hits (Pareto +12.44 vs
SelectBestCandidate +6.05 vs BeamSearch +5.11 aggregate on Qwen3-8B); greedy "led to a
local optimum after one iteration" while Pareto grows a balanced tree.

Three RuleWorld variants (same lexicon, same mock reflection LM):

* ``partial``      - default world, reward = fraction of properties right;
* ``binary``       - reward = 1 only if every property is right (coarse rewards: a
                     one-module edit can fix a minibatch example but rarely a whole validation
                     example, so accepted children often tie their parent on D_pareto - a plateau);
* ``interference`` - partial reward, 4 aspects whose general rules have hidden side
                     effects on another aspect; the reflection LM can diagnose them from the
                     foreign protocol in the output (p_diagnose = 0.25);
* ``interference_undiagnosable`` - the same with p_diagnose = 0 (commitments greedy
                     search cannot undo by reflection).

Also ``+all`` arms (module_selector="all": every module rewritten per reflection) and a
control arm ``current_best_newest_tie`` (argmax aggregate, ties broken toward the newest
candidate instead of the oldest). It separates the effect of the Pareto frontier from the
effect of tie-breaking on a validation plateau: the reference ``CurrentBest`` (``idxmax``)
returns the oldest tied candidate, while set-cover pruning of fully tied candidates leaves
the newest one.
Reports final true test score, tree shape (depth, branching, leaves, share of
iterations spent expanding the current best), at B in {1500, 4000}.

    python experiments/gepa/e3_selection.py [--llm sim|claude:haiku] [--seeds N] [--quick]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import RESULTS, fmt, paired, parse_args, pool_map, reflection_llm, save, summarize  # noqa

import numpy as np  # noqa: E402

from rsi.domains.ruleworld import ReflectionProfile, make_domain  # noqa: E402
from rsi.gepa import Config, run, tree_metrics  # noqa: E402

WORLDS = {"partial": ({}, {}), "binary": ({"scoring": "binary"}, {}),
          "interference": ({"n_interfering": 4}, {}),
          "interference_undiagnosable": ({"n_interfering": 4}, {"p_diagnose": 0.0})}
ARMS = {"pareto": ("pareto", "round_robin"), "current_best": ("current_best", "round_robin"),
        "beam_search4": ("beam_search", "round_robin"), "epsilon_greedy": ("epsilon_greedy", "round_robin"),
        "top_k_pareto5": ("top_k_pareto", "round_robin"), "pareto+all": ("pareto", "all"),
        "current_best+all": ("current_best", "all"),
        "current_best_newest_tie": ("current_best_newest_tie", "round_robin")}


class NewestTieBest:
    """Control arm: argmax mean D_pareto score, ties broken toward the newest candidate."""

    name = "current_best_newest_tie"

    def select(self, state) -> int:
        agg = state.agg_scores()
        top = max(agg)
        return max(k for k, v in enumerate(agg) if v == top)
ARGS = None


def job(spec):
    world, arm, seed, B = spec
    sel, mod = ARMS[arm]
    wkw, pkw = WORLDS[world]
    d = make_domain(seed=seed, **wkw)
    llm = reflection_llm(ARGS.llm, d.world, profile=ReflectionProfile(**pkw)) if ARGS.llm == "sim" else \
        reflection_llm(ARGS.llm)
    custom = NewestTieBest() if sel == "current_best_newest_tie" else None
    res = run(d, d.seed_artifact(), llm_propose=llm, selector=custom,
              config=Config(max_metric_calls=B, seed=seed, candidate_selection="current_best" if custom else sel,
                            module_selector=mod))
    st = res.state
    tm = tree_metrics(st)
    best = st.best_idx()
    return {"world": world, "arm": arm, "seed": seed, "B": B, "final_test": d.expected(res.best, "test"),
            "best_val": res.meta["best_val"], "stalled_iters": st.i - st.discovery_iter[best],
            "iterations": st.i + 1, "oracle": d.expected(d.oracle_artifact(), "test"), **tm}


def plot_grouped(out: dict, budgets, worlds, arms) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, len(budgets), figsize=(6.5 * len(budgets), 4), squeeze=False)
    width = 0.8 / len(arms)
    for ax, B in zip(axes[0], budgets):
        for j, arm in enumerate(arms):
            st = [out["summary"][f"{w}@B={B}"][arm]["final_test"] for w in worlds]
            xs = [i + (j - (len(arms) - 1) / 2) * width for i in range(len(worlds))]
            ax.bar(xs, [x["mean"] for x in st], width, label=arm,
                   yerr=[[x["mean"] - x["lo"] for x in st], [x["hi"] - x["mean"] for x in st]], capsize=2)
        ax.set_xticks(range(len(worlds)))
        ax.set_xticklabels([w.replace("_", "\n") for w in worlds], fontsize=8)
        ax.set_title(f"B = {B} rollouts", fontsize=10)
        ax.set_ylabel("true test score")
        ax.grid(axis="y", alpha=0.3)
    handles, labels = axes[0][0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=len(arms), fontsize=7, frameon=False)
    fig.suptitle("E3: candidate selection strategy x RuleWorld variant (mean, 95% bootstrap CI, 30 seeds)",
                 fontsize=10)
    fig.tight_layout(rect=(0, 0.07, 1, 1))
    fig.savefig(RESULTS / "e3_selection.png", dpi=130)
    plt.close(fig)
    print(f"[saved] {RESULTS / 'e3_selection.png'}")


def main():
    global ARGS
    if "--replot" in sys.argv:
        import json
        o = json.loads((RESULTS / "e3_selection.json").read_text())
        wl = [w for w in WORLDS if any(k.startswith(w + "@") for k in o["summary"])]
        return plot_grouped(o, o["config"]["budgets"], wl, list(next(iter(o["summary"].values()))))
    ARGS = a = parse_args("E3: candidate selection strategies", default_seeds=30)
    budgets = [400] if a.live else ([1500] if a.quick else [1500, 4000])
    arms = list(ARMS) if not a.live else ["pareto", "current_best", "current_best_newest_tie"]
    worlds = list(WORLDS) if not a.live else ["binary"]
    jobs = [(w, arm, s, B) for B in budgets for w in worlds for arm in arms for s in range(a.seeds)]
    rows = pool_map(job, jobs, a.workers)
    out = {"experiment": "E3 candidate selection", "llm": a.llm, "config": {"budgets": budgets, "seeds": a.seeds,
                                                                            "worlds": WORLDS, "arms": ARMS},
           "summary": {}, "paired_vs_pareto": {}, "raw": rows}
    verdicts = []
    for B in budgets:
        for w in worlds:
            key = f"{w}@B={B}"
            by = {arm: sorted([r for r in rows if r["world"] == w and r["B"] == B and r["arm"] == arm],
                              key=lambda r: r["seed"]) for arm in arms}
            out["summary"][key] = {arm: {m: summarize([r[m] for r in rs]) for m in
                                         ("final_test", "best_val", "max_depth", "mean_branching", "n_leaves",
                                          "frac_selected_top", "distinct_parents", "stalled_iters", "n_candidates")}
                                   for arm, rs in by.items()}
            out["paired_vs_pareto"][key] = {arm: paired([r["final_test"] for r in by[arm]],
                                                        [r["final_test"] for r in by["pareto"]])
                                            for arm in arms if arm != "pareto"}
            s = out["summary"][key]
            best_arm = max(arms, key=lambda k: s[k]["final_test"]["mean"])
            pc = out["paired_vs_pareto"][key].get("current_best", {})
            pn = out["paired_vs_pareto"][key].get("current_best_newest_tie", {})
            verdicts.append(
                f"[{key}] Pareto {fmt(s['pareto']['final_test'])} vs CurrentBest {fmt(s['current_best']['final_test'])}"
                + (f", BeamSearch(4) {fmt(s['beam_search4']['final_test'])}" if "beam_search4" in s else "")
                + f"; Pareto - CurrentBest = {pc.get('mean_diff', float('nan')):+.3f} "
                  f"[{pc.get('lo', float('nan')):+.3f}, {pc.get('hi', float('nan')):+.3f}]"
                + (f"; Pareto - CurrentBest(newest tie) = {pn['mean_diff']:+.3f} [{pn['lo']:+.3f}, {pn['hi']:+.3f}]"
                   if pn else "")
                + f"; best arm: {best_arm}. "
                f"Tree: Pareto depth {s['pareto']['max_depth']['mean']:.1f}, leaves {s['pareto']['n_leaves']['mean']:.1f}, "
                f"expands current best {s['pareto']['frac_selected_top']['mean']:.0%} vs CurrentBest depth "
                f"{s['current_best']['max_depth']['mean']:.1f}, leaves {s['current_best']['n_leaves']['mean']:.1f}, "
                f"stalled for the last {s['current_best']['stalled_iters']['mean']:.0f} of "
                f"{np.mean([r['iterations'] for r in by['current_best']]):.0f} iterations.")
    wins = {k: v.get("current_best", {}).get("lo", -1) > 0 for k, v in out["paired_vs_pareto"].items()}
    losses = {k: v.get("current_best", {}).get("hi", 1) < 0 for k, v in out["paired_vs_pareto"].items()}
    repro = sorted({k.split("@")[0] for k, v in wins.items() if v})
    notrep = sorted({k.split("@")[0] for k in wins} - set(repro))
    tie = {k: v.get("current_best_newest_tie", {}) for k, v in out["paired_vs_pareto"].items()}
    tie_wins = [k for k, v in tie.items() if v.get("lo", -1) > 0]
    out["tie_control"] = {"pareto_beats_newest_tie_best": tie_wins,
                          "pareto_loses_to_newest_tie_best": [k for k, v in tie.items() if v.get("hi", 1) < 0]}
    out["verdict"] = (" ".join(verdicts) + f" Pareto significantly beats CurrentBest in "
                      f"{[k for k, v in wins.items() if v]}; significantly loses in {[k for k, v in losses.items() if v]}. "
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
