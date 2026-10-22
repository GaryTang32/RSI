"""M3 - Accuracy-context trade-off with a Pareto frontier.

    python experiments/metaharness-solpi/m3_pareto.py [--llm sim|claude:haiku] [--seeds N] [--quick]
"""
from __future__ import annotations

import sys
from functools import partial
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import (figure_path, fmt, fresh_dir, live_llm, paired, parse_args, plt, pool_map, save,  # noqa: E402
                     summarize, table)
from mh_common import drop_traces  # noqa: E402

import numpy as np  # noqa: E402

from rsi.core import Evaluator  # noqa: E402
from rsi.domains.memoclassify import make_domain  # noqa: E402
from rsi.metaharness import Config, hypervolume, pareto_frontier, run  # noqa: E402
from rsi.metaharness.store import context_mean  # noqa: E402

ARMS = {"pareto": ("score", "context_cost"), "scalar": ("score",)}
SUMMARY_KEYS = ("frontier_size", "hypervolume", "selected_search", "selected_test", "selected_context",
                "context_ratio_selected", "n_frontier_beats_fewshot_all", "selected_minus_best_fewshot_test",
                "context_ratio_vs_best_fewshot", "selected_minus_best_cheaper_fewshot_test")
SHARE_KEYS = ("any_beats_fewshot_all_search", "any_beats_fewshot_all_test", "any_beats_best_fewshot_test")


def n_iterations(args) -> int:
    return 2 if args.live else (4 if args.quick else 8)


def fewshot_variants(dom, loop, fa: dict, fa_t: dict) -> dict:
    """fewshot_all and the few-shot-N comparators on search and test, evaluated after the search (never seen by
    the loop): fewshot_all overflows MemoLM-A's context budget, so it flatters any retrieval harness."""
    ev_s = Evaluator(dom, dom.make_model("A"), workers=1)
    ev_t = Evaluator(dom, dom.make_model("A"), workers=1, allow_sealed=True)
    fs = {"fewshot_all": {"search": fa["score"], "search_context": fa["context_cost"], "test": fa_t["score"],
                          "test_context": fa_t["context_cost"]}}
    for nm, art in dom.comparators().items():
        rs, rt = ev_s.evaluate(art, "evolve"), ev_t.evaluate(art, "test")
        fs[nm] = {"search": rs.score, "search_context": context_mean(loop._cost(rs)), "test": rt.score,
                  "test_context": context_mean(loop._cost(rt))}
    return fs


def job(args, spec) -> dict:
    arm, seed = spec
    dom = make_domain(seed=seed, scale=0.5 if args.live else 1.0)
    res = run(dom, dom.seed_artifact("fewshot_all"), llm_task=dom.make_model("A"),
              llm_propose=live_llm(args.llm) if args.live else None,
              config=Config(iterations=n_iterations(args), k=2, objectives=ARMS[arm], seed=seed),
              out_dir=fresh_dir("m3", f"{arm}_s{seed}_{args.llm.replace(':', '_')}"), baselines=dom.baselines())
    st = res.loop.store
    pts = [(nm, st.scores(nm)["score"], st.scores(nm)["context_cost"]) for nm in st.names() if st.scores(nm)]
    fa = st.scores("fewshot_all")
    ref = 1.1 * fa["context_cost"]
    front = pareto_frontier(pts)
    beats = [p for p in front if p[1] > fa["score"] and p[2] < fa["context_cost"]]
    best = max(pts, key=lambda p: (p[1], -p[2]))
    final = res.meta["final"]["splits"]["test"]["results"]
    fa_t = final["fewshot_all"]
    front_test = pareto_frontier([(s, r["score"], r["context_cost"]) for s, r in final.items()])
    beats_test = [p for p in front_test if p[1] > fa_t["score"] and p[2] < fa_t["context_cost"]]
    fs = fewshot_variants(dom, res.loop, fa, fa_t)

    def rank(n: str) -> tuple:                     # chosen on search
        return fs[n]["search"], -fs[n]["search_context"]

    best_fs = max(fs, key=rank)
    cheaper = [n for n in fs if fs[n]["search_context"] <= best[2]]
    best_cheaper = max(cheaper, key=rank) if cheaper else None
    sel_t = final.get(best[0], {}).get("score")
    beats_best_fs_test = [p for p in front_test if p[1] > fs[best_fs]["test"] and p[2] < fs[best_fs]["test_context"]]
    drop_traces(st.root)
    return {"arm": arm, "seed": seed, "points": pts, "frontier": front, "frontier_size": len(front),
            "fewshot_variants": fs, "best_fewshot": best_fs,
            "selected_minus_best_fewshot_test": (sel_t - fs[best_fs]["test"]) if sel_t is not None else None,
            "context_ratio_vs_best_fewshot": fs[best_fs]["search_context"] / max(1.0, best[2]),
            "best_cheaper_fewshot": best_cheaper,
            "selected_minus_best_cheaper_fewshot_test": (sel_t - fs[best_cheaper]["test"])
            if (sel_t is not None and best_cheaper) else None,
            "any_beats_best_fewshot_test": bool(beats_best_fs_test),
            "hypervolume": hypervolume(pts, ref_cost=ref), "hv_ref_cost": ref,
            "selected": best[0], "selected_search": best[1], "selected_context": best[2],
            "selected_test": sel_t, "selected_test_context": final.get(best[0], {}).get("context_cost"),
            "fewshot_all_search": fa["score"], "fewshot_all_context": fa["context_cost"],
            "fewshot_all_test": fa_t["score"], "n_frontier_beats_fewshot_all": len(beats),
            "any_beats_fewshot_all_search": bool(beats), "any_beats_fewshot_all_test": bool(beats_test),
            "context_ratio_selected": fa["context_cost"] / max(1.0, best[2])}


def make_verdict(p: dict, cmp: dict) -> str:
    several = p["frontier_size"]["mean"] >= 2
    beats = p["share_any_beats_fewshot_all_test"] >= 0.5
    return (("REPRODUCED" if several and beats else "PARTIAL" if several or beats else "NOT REPRODUCED")
            + f": Pareto frontier has {p['frontier_size']['mean']:.1f} non-dominated harnesses on average; "
            f"in {100 * p['share_any_beats_fewshot_all_test']:.0f}% of runs a frontier harness beats "
            f"fewshot_all on TEST accuracy with less context (selected harness uses "
            f"{p['context_ratio_selected']['mean']:.1f}x less context). Pareto vs scalar hypervolume "
            f"diff = {cmp['hypervolume'].get('mean_diff', float('nan')):+.1f}. Against the best few-shot-N variant "
            f"(chosen on search; fewshot_all overflows the model's budget): selected - best few-shot on test = "
            f"{fmt(p['selected_minus_best_fewshot_test'])}, context ratio "
            f"{p['context_ratio_vs_best_fewshot']['mean']:.1f}x; vs the best few-shot variant with no more "
            f"context than the selected harness: {fmt(p['selected_minus_best_cheaper_fewshot_test'])}; a "
            f"frontier harness beats the best few-shot variant on test with less context in "
            f"{100 * p['share_any_beats_best_fewshot_test']:.0f}% of runs")


def plot(by: dict, png: Path) -> None:
    f, ax = plt().subplots(figsize=(6.5, 4.2))
    r0 = by["pareto"][0]
    ax.scatter([p[2] for p in r0["points"]], [p[1] for p in r0["points"]], s=18, c="lightgray",
               label="all candidates (pareto arm, seed 0)")
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
    f.savefig(png, dpi=120)


def main():
    args = parse_args(__doc__.splitlines()[0])
    rows = pool_map(partial(job, args), [(a, s) for s in range(args.seeds) for a in ARMS], args.workers)
    by = {a: sorted([r for r in rows if r["arm"] == a], key=lambda r: r["seed"]) for a in ARMS}
    summ = {a: {k: summarize([r[k] for r in by[a]]) for k in SUMMARY_KEYS} for a in ARMS}
    for a in ARMS:
        for k in SHARE_KEYS:
            summ[a][f"share_{k}"] = float(np.mean([r[k] for r in by[a]]))
        summ[a]["best_fewshot_counts"] = {n: sum(r["best_fewshot"] == n for r in by[a])
                                          for n in sorted({r["best_fewshot"] for r in by[a]})}
    cmp = {k: paired([r[k] for r in by["scalar"]], [r[k] for r in by["pareto"]]) for k in
           ("frontier_size", "hypervolume", "selected_test")}
    verdict = make_verdict(summ["pareto"], cmp)
    print(table([[a] + [fmt(summ[a][k], 2) for k in ("frontier_size", "hypervolume", "selected_test",
                                                     "context_ratio_selected")] for a in ARMS],
                ["arm", "frontier size", "hypervolume", "selected test acc", "context reduction x"]))
    print("verdict:", verdict)
    png = figure_path("m3_pareto", args)
    plot(by, png)
    save("m3_pareto" + ("_live" if args.live else ""), {
        "claim": "Pareto frontier over (accuracy, context); a selected harness beats fewshot_all with less context "
                 "[MH Table 2/9]",
        "config": {"llm": args.llm, "seeds": args.seeds}, "per_seed": rows, "summary": summ,
        "paired_pareto_minus_scalar": cmp, "verdict": verdict, "figure": str(png)}, args.out)


if __name__ == "__main__":
    main()
