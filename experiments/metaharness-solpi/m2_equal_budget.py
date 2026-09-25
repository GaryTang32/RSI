"""M2 - Meta-Harness vs other text optimisers at an equal budget of candidate evaluations.

Claim [paper:MH Table 4, Fig. 1/4]: with the same proposer and the same number of full evaluations,
Meta-Harness (full history) beats Best-of-N (independent samples from the seed), OPRO-style windowed
(solution, score) history and GEPA/TextGrad-style reflection on the current candidate's traces; it matches
the others' final quality with far fewer evaluations.

All arms are the SAME loop and proposer; only the history view differs: ``seed_only`` (Best-of-N), ``window``
(last w = 4 candidates' code + score), ``last_only`` (current best's code + traces), ``full``.

    python experiments/metaharness-solpi/m2_equal_budget.py [--llm sim|claude:haiku] [--seeds N] [--quick]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import RESULTS, figure_path, fmt, fresh_dir, live_llm, paired, parse_args, plt, pool_map, save, summarize, \
    table  # noqa: E402

import numpy as np  # noqa: E402

from rsi.domains.memoclassify import make_domain  # noqa: E402
from rsi.metaharness import Config, run  # noqa: E402

ARMS = {"best_of_n": "seed_only", "opro_window": "window", "gepa_reflect": "last_only", "metaharness": "full"}
ARGS = None


def job(spec):
    arm, seed = spec
    dom = make_domain(seed=seed, scale=0.5 if ARGS.live else 1.0)
    budget = 4 if ARGS.live else (8 if ARGS.quick else 20)
    res = run(dom, dom.seed_artifact("fewshot_all"), llm_task=dom.make_model("A"),
              llm_propose=live_llm(ARGS.llm) if ARGS.live else None,
              config=Config(iterations=budget, k=2, history_mode=ARMS[arm], window=4, eval_budget=budget, seed=seed),
              out_dir=fresh_dir("m2", f"{arm}_s{seed}_{ARGS.llm.replace(':', '_')}"), baselines=dom.baselines())
    base_best = max(res.loop.store.scores(n)["score"] for n in dom.baselines())
    curve = [base_best] + [c["best_so_far"] for c in res.meta["curve"]]
    curve += [curve[-1]] * (budget + 1 - len(curve))
    best = res.meta["best_system"]
    test = res.meta["final"]["splits"]["test"]["results"]
    return {"arm": arm, "seed": seed, "curve": curve, "final_best": curve[-1], "selected": best,
            "selected_test": test.get(best, {}).get("score"), "fewshot_all_test": test["fewshot_all"]["score"],
            "n_evaluated": res.meta["n_evaluated"]}


def evals_to_reach(curve, target):
    for i, v in enumerate(curve):
        if v >= target - 1e-12:
            return i
    return None


def main():
    global ARGS
    ARGS = parse_args(__doc__.splitlines()[0])
    jobs = [(a, s) for s in range(ARGS.seeds) for a in ARMS]
    rows = pool_map(job, jobs, ARGS.workers)
    by = {a: sorted([r for r in rows if r["arm"] == a], key=lambda r: r["seed"]) for a in ARMS}
    summ = {a: {k: summarize([r[k] for r in by[a]]) for k in ("final_best", "selected_test")} for a in ARMS}
    reach = {}
    for a in ARMS:
        if a == "metaharness":
            continue
        # evaluations Meta-Harness needs to match this arm's FINAL best (per seed)
        reach[a] = [evals_to_reach(m["curve"], o["final_best"]) for m, o in zip(by["metaharness"], by[a])]
    cmp = {a: {k: paired([r[k] for r in by[a]], [r[k] for r in by["metaharness"]]) for k in
               ("final_best", "selected_test")} for a in ARMS if a != "metaharness"}
    wins = all(cmp[a]["final_best"].get("mean_diff", 0) > 0 for a in cmp)
    sig = all(cmp[a]["final_best"].get("lo", -1) > 0 for a in cmp)
    budget = len(by["metaharness"][0]["curve"]) - 1
    verdict = ("REPRODUCED" if wins and sig else "PARTIAL" if wins else "NOT REPRODUCED") + \
        f": Meta-Harness final best vs others (paired diffs) = " + \
        ", ".join(f"{a}: {cmp[a]['final_best'].get('mean_diff', float('nan')):+.3f}" for a in cmp)
    reach_summary = {a: {"median_when_reached": float(np.median([x for x in v if x is not None]))
                         if any(x is not None for x in v) else None,
                         "n_seeds_never_reached": sum(x is None for x in v), "n_seeds": len(v)}
                     for a, v in reach.items()}
    # the paper's "matches the others with ~10x fewer evaluations" = MH reaches their FINAL value within
    # budget/10 evaluations (in the median seed; a seed where MH never matches counts as budget + 1)
    tenx = {a: float(np.median([x if x is not None else budget + 1 for x in v])) <= budget / 10
            for a, v in reach.items()}
    verdict += "; 10x-fewer-evaluations claim " + ("reproduced" if all(tenx.values()) else "NOT reproduced") + \
        " (median evaluations MH needs to match each arm's final best: " + \
        ", ".join(f"{a}: {float(np.median([x if x is not None else budget + 1 for x in v])):.1f}"
                  for a, v in reach.items()) + f" of {budget}; never matched in " + \
        ", ".join(f"{a}: {reach_summary[a]['n_seeds_never_reached']}/{len(v)}" for a, v in reach.items()) + " seeds)"
    print(table([[a, fmt(summ[a]["final_best"]), fmt(summ[a]["selected_test"]),
                  f"{reach_summary[a]['median_when_reached']}" if a in reach_summary else "-"]
                 for a in ARMS], ["arm", "final best (search)", "selected (test)", "MH evals to match (median)"]))
    print("verdict:", verdict)
    fig = plt()
    f, ax = fig.subplots(figsize=(6.5, 4))
    for a in ARMS:
        m = np.mean([r["curve"] for r in by[a]], axis=0)
        ax.plot(range(len(m)), m, label=a, lw=2 if a == "metaharness" else 1.2)
    ax.set_xlabel("candidate evaluations")
    ax.set_ylabel("best-so-far search accuracy (mean over seeds)")
    ax.set_title(f"M2 equal budget ({ARGS.llm}, {ARGS.seeds} seeds, budget {budget})")
    ax.legend()
    f.tight_layout()
    png = figure_path("m2_equal_budget", ARGS)
    RESULTS.mkdir(parents=True, exist_ok=True)
    f.savefig(png, dpi=120)
    save("m2_equal_budget" + ("_live" if ARGS.live else ""), {
        "claim": "At equal evaluation budget Meta-Harness beats Best-of-N / OPRO-window / GEPA-style reflection "
                 "[MH Table 4]",
        "config": {"llm": ARGS.llm, "seeds": ARGS.seeds, "budget": budget, "k": 2, "window": 4},
        "per_seed": rows, "summary": summ, "paired_vs_metaharness": cmp,
        "metaharness_evals_to_match_final": reach, "evals_to_match_summary": reach_summary,
        "verdict": verdict, "figure": str(png),
        "caveat": "Offline arms share one deterministic MockProposer; differences come only from the history view. "
                  "Best-of-N samples independently from the run's seed harness (fewshot_all) only."},
        ARGS.out)


if __name__ == "__main__":
    main()
