"""M2 - Meta-Harness vs other text optimisers at an equal budget of candidate evaluations.

    python experiments/metaharness-solpi/m2_equal_budget.py [--llm sim|claude:haiku] [--seeds N] [--quick]
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

from rsi.domains.memoclassify import make_domain  # noqa: E402
from rsi.metaharness import Config, run  # noqa: E402

# same loop and proposer in every arm; only the history view differs
ARMS = {"best_of_n": "seed_only", "opro_window": "window", "gepa_reflect": "last_only", "metaharness": "full"}
OTHERS = [a for a in ARMS if a != "metaharness"]


def job(args, spec) -> dict:
    arm, seed = spec
    dom = make_domain(seed=seed, scale=0.5 if args.live else 1.0)
    budget = 4 if args.live else (8 if args.quick else 20)
    res = run(dom, dom.seed_artifact("fewshot_all"), llm_task=dom.make_model("A"),
              llm_propose=live_llm(args.llm) if args.live else None,
              config=Config(iterations=budget, k=2, history_mode=ARMS[arm], window=4, eval_budget=budget, seed=seed),
              out_dir=fresh_dir("m2", f"{arm}_s{seed}_{args.llm.replace(':', '_')}"), baselines=dom.baselines())
    curve = [max(res.loop.store.scores(n)["score"] for n in dom.baselines())] + \
        [c["best_so_far"] for c in res.meta["curve"]]
    curve += [curve[-1]] * (budget + 1 - len(curve))
    best = res.meta["best_system"]
    test = res.meta["final"]["splits"]["test"]["results"]
    drop_traces(res.loop.store.root)
    return {"arm": arm, "seed": seed, "curve": curve, "final_best": curve[-1], "selected": best,
            "selected_test": test.get(best, {}).get("score"), "fewshot_all_test": test["fewshot_all"]["score"],
            "n_evaluated": res.meta["n_evaluated"]}


def evals_to_reach(curve: list[float], target: float) -> int | None:
    return next((i for i, v in enumerate(curve) if v >= target - 1e-12), None)


def reach_stats(reach: dict, budget: int) -> dict:
    """Medians of the evaluations MH needs to match an arm's final best: over the seeds where it does, and over
    all seeds with a never-matched seed counted as budget + 1."""
    out = {}
    for a, v in reach.items():
        hit = [x for x in v if x is not None]
        out[a] = {"median_when_reached": float(np.median(hit)) if hit else None,
                  "median_never_as_budget_plus_1": float(np.median([x if x is not None else budget + 1 for x in v]))
                  if v else None,
                  "n_seeds_never_reached": sum(x is None for x in v), "n_seeds": len(v)}
    return out


def make_verdict(cmp: dict, reach_summary: dict, budget: int) -> str:
    wins = all(cmp[a]["final_best"].get("mean_diff", 0) > 0 for a in cmp)
    sig = all(cmp[a]["final_best"].get("lo", -1) > 0 for a in cmp)
    # the paper's "~10x fewer evaluations": MH reaches each arm's FINAL value within budget/10 (median seed)
    tenx = all(reach_summary[a]["median_never_as_budget_plus_1"] <= budget / 10 for a in reach_summary)
    diffs = ", ".join(f"{a}: {cmp[a]['final_best'].get('mean_diff', float('nan')):+.3f}" for a in cmp)
    medians = ", ".join(f"{a}: {r['median_never_as_budget_plus_1']:.1f} / {r['median_when_reached']}"
                        for a, r in reach_summary.items())
    never = ", ".join(f"{a}: {r['n_seeds_never_reached']}/{r['n_seeds']}" for a, r in reach_summary.items())
    return (("REPRODUCED" if wins and sig else "PARTIAL" if wins else "NOT REPRODUCED")
            + f": Meta-Harness final best vs others (paired diffs) = {diffs}"
            + "; 10x-fewer-evaluations claim " + ("reproduced" if tenx else "NOT reproduced")
            + f" (evaluations MH needs to match each arm's final best, of {budget}: median over all seeds with "
              f"never-matched seeds counted as {budget + 1} / median over the seeds where MH matches: {medians}"
              f"; never matched in {never} seeds)")


def main():
    args = parse_args(__doc__.splitlines()[0])
    rows = pool_map(partial(job, args), [(a, s) for s in range(args.seeds) for a in ARMS], args.workers)
    by = {a: sorted([r for r in rows if r["arm"] == a], key=lambda r: r["seed"]) for a in ARMS}
    summ = {a: {k: summarize([r[k] for r in by[a]]) for k in ("final_best", "selected_test")} for a in ARMS}
    # evaluations MH needs to match each arm's FINAL best (per seed)
    reach = {a: [evals_to_reach(m["curve"], o["final_best"]) for m, o in zip(by["metaharness"], by[a])]
             for a in OTHERS}
    cmp = {a: {k: paired([r[k] for r in by[a]], [r[k] for r in by["metaharness"]])
               for k in ("final_best", "selected_test")} for a in OTHERS}
    budget = len(by["metaharness"][0]["curve"]) - 1
    reach_summary = reach_stats(reach, budget)
    verdict = make_verdict(cmp, reach_summary, budget)
    print(table([[a, fmt(summ[a]["final_best"]), fmt(summ[a]["selected_test"]),
                  (f"{reach_summary[a]['median_never_as_budget_plus_1']} / {reach_summary[a]['median_when_reached']}"
                   if a in reach_summary else "-")] for a in ARMS],
                ["arm", "final best (search)", "selected (test)",
                 "MH evals to match (median all seeds / reached seeds)"]))
    print("verdict:", verdict)
    png = figure_path("m2_equal_budget", args)
    f, ax = plt().subplots(figsize=(6.5, 4))
    for a in ARMS:
        m = np.mean([r["curve"] for r in by[a]], axis=0)
        ax.plot(range(len(m)), m, label=a, lw=2 if a == "metaharness" else 1.2)
    ax.set_xlabel("candidate evaluations")
    ax.set_ylabel("best-so-far search accuracy (mean over seeds)")
    ax.set_title(f"M2 equal budget ({args.llm}, {args.seeds} seeds, budget {budget})")
    ax.legend()
    f.tight_layout()
    f.savefig(png, dpi=120)
    save("m2_equal_budget" + ("_live" if args.live else ""), {
        "claim": "At equal evaluation budget Meta-Harness beats Best-of-N / OPRO-window / GEPA-style reflection "
                 "[MH Table 4]",
        "config": {"llm": args.llm, "seeds": args.seeds, "budget": budget, "k": 2, "window": 4},
        "per_seed": rows, "summary": summ, "paired_vs_metaharness": cmp,
        "metaharness_evals_to_match_final": reach, "evals_to_match_summary": reach_summary,
        "verdict": verdict, "figure": str(png),
        "caveat": "Offline arms share one deterministic MockProposer; differences come only from the history view. "
                  "Best-of-N samples independently from the run's seed harness (fewshot_all) only."},
        args.out)


if __name__ == "__main__":
    main()
