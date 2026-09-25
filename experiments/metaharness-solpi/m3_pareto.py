"""M3 - Accuracy-context trade-off with a Pareto frontier.

Claim [paper:MH §4.1, Table 2/9, Fig. 3]: Meta-Harness evaluates candidates under Pareto dominance (accuracy
up, context cost down) and reports a frontier of several non-dominated harnesses; the selected harness beats
the few-shot-all baseline in accuracy with (much) less context ("+7.7 points with 4x fewer context tokens").

Arms: Pareto objective ("score", "context_cost") vs scalar objective ("score",) - same proposer, the frontier
file the proposer reads differs. Metrics per run: frontier size and hypervolume over ALL evaluated candidates
(common reference = 1.1 x fewshot_all context), the selected harness' accuracy and context vs fewshot_all on
search and test, and whether some frontier point beats fewshot_all in accuracy at lower context.

    python experiments/metaharness-solpi/m3_pareto.py [--llm sim|claude:haiku] [--seeds N] [--quick]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import RESULTS, fmt, fresh_dir, live_llm, paired, parse_args, plt, pool_map, save, summarize, \
    table  # noqa: E402

import numpy as np  # noqa: E402

from rsi.domains.memoclassify import make_domain  # noqa: E402
from rsi.metaharness import Config, hypervolume, pareto_frontier, run  # noqa: E402

ARMS = {"pareto": ("score", "context_cost"), "scalar": ("score",)}
ARGS = None


def job(spec):
    arm, seed = spec
    dom = make_domain(seed=seed, scale=0.5 if ARGS.live else 1.0)
    n = 2 if ARGS.live else (4 if ARGS.quick else 8)
    res = run(dom, dom.seed_artifact("fewshot_all"), llm_task=dom.make_model("A"),
              llm_propose=live_llm(ARGS.llm) if ARGS.live else None,
              config=Config(iterations=n, k=2, objectives=ARMS[arm], seed=seed),
              out_dir=fresh_dir("m3", f"{arm}_s{seed}_{ARGS.llm.replace(':', '_')}"), baselines=dom.baselines())
    st = res.loop.store
    pts = [(nm, st.scores(nm)["score"], st.scores(nm)["context_cost"]) for nm in st.names() if st.scores(nm)]
    fa = st.scores("fewshot_all")
    ref = 1.1 * fa["context_cost"]
    front = pareto_frontier(pts)
    beats = [p for p in front if p[1] > fa["score"] and p[2] < fa["context_cost"]]
    best = max(pts, key=lambda p: (p[1], -p[2]))
    final = res.meta["final"]["splits"]["test"]["results"]
    fa_t = final["fewshot_all"]
    front_test = [(s, r["score"], r["context_cost"]) for s, r in final.items()]
    beats_test = [p for p in pareto_frontier(front_test) if p[1] > fa_t["score"] and p[2] < fa_t["context_cost"]]
    return {"arm": arm, "seed": seed, "points": pts, "frontier": front, "frontier_size": len(front),
            "hypervolume": hypervolume(pts, ref_cost=ref), "hv_ref_cost": ref,
            "selected": best[0], "selected_search": best[1], "selected_context": best[2],
            "selected_test": final.get(best[0], {}).get("score"),
            "selected_test_context": final.get(best[0], {}).get("context_cost"),
            "fewshot_all_search": fa["score"], "fewshot_all_context": fa["context_cost"],
            "fewshot_all_test": fa_t["score"], "n_frontier_beats_fewshot_all": len(beats),
            "any_beats_fewshot_all_search": bool(beats), "any_beats_fewshot_all_test": bool(beats_test),
            "context_ratio_selected": fa["context_cost"] / max(1.0, best[2])}


def main():
    global ARGS
    ARGS = parse_args(__doc__.splitlines()[0])
    rows = pool_map(job, [(a, s) for s in range(ARGS.seeds) for a in ARMS], ARGS.workers)
    by = {a: sorted([r for r in rows if r["arm"] == a], key=lambda r: r["seed"]) for a in ARMS}
    keys = ("frontier_size", "hypervolume", "selected_search", "selected_test", "selected_context",
            "context_ratio_selected", "n_frontier_beats_fewshot_all")
    summ = {a: {k: summarize([r[k] for r in by[a]]) for k in keys} for a in ARMS}
    for a in ARMS:
        summ[a]["share_any_beats_fewshot_all_search"] = float(np.mean([r["any_beats_fewshot_all_search"] for r in by[a]]))
        summ[a]["share_any_beats_fewshot_all_test"] = float(np.mean([r["any_beats_fewshot_all_test"] for r in by[a]]))
    cmp = {k: paired([r[k] for r in by["scalar"]], [r[k] for r in by["pareto"]]) for k in
           ("frontier_size", "hypervolume", "selected_test")}
    several = summ["pareto"]["frontier_size"]["mean"] >= 2
    beats = summ["pareto"]["share_any_beats_fewshot_all_test"] >= 0.5
    verdict = ("REPRODUCED" if several and beats else "PARTIAL" if several or beats else "NOT REPRODUCED") + \
        f": Pareto frontier has {summ['pareto']['frontier_size']['mean']:.1f} non-dominated harnesses on average; " \
        f"in {100 * summ['pareto']['share_any_beats_fewshot_all_test']:.0f}% of runs a frontier harness beats " \
        f"fewshot_all on TEST accuracy with less context (selected harness uses " \
        f"{summ['pareto']['context_ratio_selected']['mean']:.1f}x less context). Pareto vs scalar hypervolume " \
        f"diff = {cmp['hypervolume'].get('mean_diff', float('nan')):+.1f}"
    print(table([[a] + [fmt(summ[a][k], 2) for k in ("frontier_size", "hypervolume", "selected_test",
                                                     "context_ratio_selected")] for a in ARMS],
                ["arm", "frontier size", "hypervolume", "selected test acc", "context reduction x"]))
    print("verdict:", verdict)
    fig = plt()
    f, ax = fig.subplots(figsize=(6.5, 4.2))
    r0 = by["pareto"][0]
    xs = [p[2] for p in r0["points"]]
    ys = [p[1] for p in r0["points"]]
    ax.scatter(xs, ys, s=18, c="lightgray", label="all candidates (pareto arm, seed 0)")
    for a, c in (("pareto", "C0"), ("scalar", "C3")):
        fr = sorted(by[a][0]["frontier"], key=lambda p: p[2])
        ax.plot([p[2] for p in fr], [p[1] for p in fr], "-o", c=c, label=f"frontier ({a})")
    ax.scatter([r0["fewshot_all_context"]], [r0["fewshot_all_search"]], marker="*", s=160, c="k",
               label="fewshot_all")
    ax.set_xlabel("context cost (injected chars per query)")
    ax.set_ylabel("search accuracy")
    ax.set_title("M3 accuracy-context frontier")
    ax.legend(fontsize=7)
    f.tight_layout()
    png = RESULTS / "m3_pareto.png"
    RESULTS.mkdir(parents=True, exist_ok=True)
    f.savefig(png, dpi=120)
    save("m3_pareto" + ("_live" if ARGS.live else ""), {
        "claim": "Pareto frontier over (accuracy, context); a selected harness beats fewshot_all with less context "
                 "[MH Table 2/9]",
        "config": {"llm": ARGS.llm, "seeds": ARGS.seeds}, "per_seed": rows, "summary": summ,
        "paired_pareto_minus_scalar": cmp, "verdict": verdict, "figure": str(png)}, ARGS.out)


if __name__ == "__main__":
    main()
