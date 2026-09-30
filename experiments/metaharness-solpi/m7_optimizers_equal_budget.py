"""M7 - Meta-Harness vs structured text optimisers at an equal budget of 40 candidate evaluations (retry round 2).

Claims [paper:MH §4.1, Table 4, Figs. 1 and 4]: with the same proposer, selection on the search set only and the
same budget of harness evaluations, Meta-Harness beats Best-of-N, OpenEvolve, TTT-Discover and GEPA; it "matches
the next-best method's final accuracy after just 4 evaluations" and ends "more than 10 points" above.

Unlike M2 (view projections only), the comparison arms here apply their optimiser's own parent-selection rule
and context (``rsi.metaharness.baselines.StructuredOptimizerProposer``); Best-of-N is the ``seed_only`` view.
Every arm uses the same offline MockProposer. Preregistered in the claims audit (P1).

    python experiments/metaharness-solpi/m7_optimizers_equal_budget.py --seeds 20 --batch 0/2 --workers 2
    python experiments/metaharness-solpi/m7_optimizers_equal_budget.py --seeds 20 --batch 1/2 --workers 2
    python experiments/metaharness-solpi/m7_optimizers_equal_budget.py --seeds 20 --merge
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import RESULTS, fmt, fresh_dir, paired, parse_args, plt, pool_map, save, summarize, table  # noqa: E402
from mh_common import drop_traces  # noqa: E402

import numpy as np  # noqa: E402

from rsi.domains.memoclassify import make_domain  # noqa: E402
from rsi.metaharness import Config, MemoClassifyLibrary, MockProposer, StructuredOptimizerProposer, run  # noqa: E402

ARMS = ("metaharness", "best_of_n", "openevolve", "ttt_discover", "gepa")
N_ITER, K = 20, 2
BUDGET = N_ITER * K
ARGS = None


def job(spec):
    arm, seed = spec
    dom = make_domain(seed=seed)
    mode = {"metaharness": "full", "best_of_n": "seed_only"}.get(arm, "full")
    prop = MockProposer(MemoClassifyLibrary(), seed=seed)
    if arm in ("openevolve", "ttt_discover", "gepa"):
        prop = StructuredOptimizerProposer(prop, arm, seed=seed)
    res = run(dom, dom.seed_artifact("fewshot_all"), llm_task=dom.make_model("A"), proposer=prop,
              config=Config(iterations=N_ITER, k=K, history_mode=mode, eval_budget=BUDGET, seed=seed,
                            shadow_monitor=False),
              out_dir=fresh_dir("m7", f"{arm}_s{seed}"), baselines=dom.baselines())
    store = res.loop.store
    base_best = max(store.scores(n)["score"] for n in dom.baselines())
    curve = [base_best] + [c["best_so_far"] for c in res.meta["curve"]]
    curve += [curve[-1]] * (BUDGET + 1 - len(curve))
    cands = [store.scores(n)["score"] for n in store.names() if n not in dom.baselines() and store.scores(n)]
    best = res.meta["best_system"]
    test = res.meta["final"]["splits"]["test"]["results"]
    dedup = sum(int((json.loads(p.read_text()).get("proposer_meta") or {}).get("dedup_retries", 0) or 0)
                for p in store.sessions_dir().glob("iter*/meta.json"))
    drop_traces(store.root)
    return {"arm": arm, "seed": seed, "curve": curve, "final_best": curve[-1],
            "median_candidate": float(np.median(cands)) if cands else None, "n_evaluated": res.meta["n_evaluated"],
            "selected": best, "selected_test": test.get(best, {}).get("score"),
            "fewshot_all_test": test["fewshot_all"]["score"], "dedup_retries": dedup}


def evals_to_reach(curve, target):
    for i, v in enumerate(curve):
        if v >= target - 1e-12:
            return i
    return None


def analyse(rows):
    by = {a: {r["seed"]: r for r in rows if r["arm"] == a} for a in ARMS}
    seeds = sorted(set.intersection(*[set(by[a]) for a in ARMS]))
    others = [a for a in ARMS if a != "metaharness"]
    summ = {a: {k: summarize([by[a][s][k] for s in seeds]) for k in ("final_best", "median_candidate",
                                                                         "selected_test")} for a in ARMS}
    cmp = {a: {k: paired([by[a][s][k] for s in seeds], [by["metaharness"][s][k] for s in seeds])
               for k in ("final_best", "median_candidate", "selected_test")} for a in others}
    q5 = {}
    for name, pool in (("next_best_any", others), ("next_best_openevolve_ttt", ["openevolve", "ttt_discover"])):
        e, lead, which = [], [], []
        for s in seeds:
            nb = max(pool, key=lambda a: by[a][s]["final_best"])
            tgt = by[nb][s]["final_best"]
            x = evals_to_reach(by["metaharness"][s]["curve"], tgt)
            e.append(BUDGET + 1 if x is None else x)
            lead.append(by["metaharness"][s]["final_best"] - tgt)
            which.append(nb)
        q5[name] = {"evals_to_match_per_seed": e, "median_evals_to_match": float(np.median(e)),
                    "n_never": sum(x == BUDGET + 1 for x in e), "lead": summarize(lead),
                    "next_best_arm_per_seed": which,
                    "speed_ok": float(np.median(e)) <= BUDGET / 10, "lead_ok": float(np.mean(lead)) >= 0.10}
    p = q5["next_best_any"]
    q5_verdict = ("REPRODUCED" if p["speed_ok"] and p["lead_ok"] else
                  "PARTIAL" if p["speed_ok"] or p["lead_ok"] else "NOT REPRODUCED")
    all_ci = all(cmp[a][k].get("lo", -1) > 0 for a in others for k in ("final_best", "median_candidate"))
    means = all(cmp[a]["final_best"].get("mean_diff", 0) > 0 for a in others)
    q4_verdict = "REPRODUCED" if all_ci else "PARTIAL" if means else "NOT REPRODUCED"
    return by, seeds, summ, cmp, q5, q5_verdict, q4_verdict


def main():
    global ARGS

    def extra(ap):
        ap.add_argument("--batch", default=None, help="i/n: run the i-th of n seed batches")
        ap.add_argument("--merge", action="store_true", help="merge the batch files and analyse")
    ARGS = parse_args(__doc__.splitlines()[0], default_seeds=20, extra=extra)
    part = lambda i: RESULTS / f"m7_optimizers_equal_budget.part{i}.json"   # noqa: E731
    if not ARGS.merge:
        seeds = list(range(ARGS.seeds))
        tag = "all"
        if ARGS.batch:
            i, n = map(int, ARGS.batch.split("/"))
            seeds = [s for s in seeds if s % n == i]
            tag = str(i)
        rows = pool_map(job, [(a, s) for s in seeds for a in ARMS], min(ARGS.workers, 2))
        part(tag).write_text(json.dumps(rows))
        print(f"[saved] {part(tag)} ({len(rows)} runs)")
        if ARGS.batch:
            return
    rows = []
    for p in sorted(RESULTS.glob("m7_optimizers_equal_budget.part*.json")):
        rows += json.loads(p.read_text())
    by, seeds, summ, cmp, q5, q5v, q4v = analyse(rows)
    print(table([[a, fmt(summ[a]["final_best"]), fmt(summ[a]["median_candidate"]), fmt(summ[a]["selected_test"])]
                 for a in ARMS], ["arm", "final best (search)", "median candidate", "selected (test)"]))
    for a in cmp:
        print(f"MH - {a}: final {cmp[a]['final_best']}, median {cmp[a]['median_candidate']}")
    print("Q5:", json.dumps({k: {kk: vv for kk, vv in v.items() if kk != 'evals_to_match_per_seed'}
                             for k, v in q5.items()}, default=str))
    print("verdicts: Q4", q4v, "| Q5", q5v)
    fig = plt()
    f, ax = fig.subplots(figsize=(6.5, 4))
    for a in ARMS:
        m = np.mean([by[a][s]["curve"] for s in seeds], axis=0)
        ax.plot(range(len(m)), m, label=a, lw=2 if a == "metaharness" else 1.2)
    ax.set_xlabel("candidate evaluations")
    ax.set_ylabel("best-so-far search accuracy (mean over seeds)")
    ax.set_title(f"M7 equal budget, structured optimisers ({len(seeds)} seeds)")
    ax.legend(fontsize=8)
    f.tight_layout()
    png = RESULTS / "m7_optimizers_equal_budget.png"
    f.savefig(png, dpi=120)
    save("m7_optimizers_equal_budget", {
        "claim": "Q4/Q5: at an equal budget MH beats Best-of-N, OpenEvolve, TTT-Discover and GEPA; it matches the "
                 "next-best method's final accuracy within 4 evaluations and ends >10 points above [MH §4.1, Table 4, "
                 "Figs. 1, 4]",
        "preregistration": "claims-audit-metaharness.md, Retry round 2: preregistration, P1",
        "config": {"seeds": seeds, "iterations": N_ITER, "k": K, "budget": BUDGET, "proposer": "MockProposer",
                   "arms": list(ARMS), "domain": "memoclassify scale 1.0", "model": "MemoLM-A"},
        "summary": summ, "paired_mh_minus_arm": cmp, "q5": q5, "verdict_q4": q4v, "verdict_q5": q5v,
        "per_seed": rows, "figure": str(png),
        "caveat": "All arms share the offline MockProposer; they differ in selection rule and context only. Its "
                  "trace diagnosis is written by us (see M1), so the MH advantage here is the mock's designed-in "
                  "use of traces, now measured against faithful selection rules."})


if __name__ == "__main__":
    main()
