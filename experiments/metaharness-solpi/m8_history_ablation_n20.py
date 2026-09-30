"""M8 - the Table 3 history ablation at the paper's scale: N = 20 iterations x k = 2, 20 seeds (retry round 2, P2).

Same arms and proposer as M1 (scores_only, scores_summary, full; offline MockProposer with its deterministic
summary), but the paper's 40 candidates per run instead of 16. Criteria are preregistered in the claims audit.

    python experiments/metaharness-solpi/m8_history_ablation_n20.py --batch 0/2 --workers 2
    python experiments/metaharness-solpi/m8_history_ablation_n20.py --batch 1/2 --workers 2
    python experiments/metaharness-solpi/m8_history_ablation_n20.py --merge
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import RESULTS, fmt, fresh_dir, paired, parse_args, pool_map, save, summarize, table  # noqa: E402
from mh_common import drop_traces, session_stats  # noqa: E402

import numpy as np  # noqa: E402

from rsi.domains.memoclassify import make_domain  # noqa: E402
from rsi.metaharness import Config, run  # noqa: E402

ARMS = ("scores_only", "scores_summary", "full")
N_ITER, K = 20, 2


def job(spec):
    arm, seed = spec
    dom = make_domain(seed=seed)
    res = run(dom, dom.seed_artifact("fewshot_all"), llm_task=dom.make_model("A"),
              config=Config(iterations=N_ITER, k=K, history_mode=arm, seed=seed, shadow_monitor=False),
              out_dir=fresh_dir("m8", f"{arm}_s{seed}"), baselines=dom.baselines())
    st = res.loop.store
    base = {n: st.scores(n)["score"] for n in dom.baselines()}
    cands = [st.scores(n)["score"] for n in st.names() if n not in base and st.scores(n)]
    ss = session_stats(st)
    best = res.meta["best_system"]
    test = res.meta["final"]["splits"]["test"]["results"]
    drop_traces(st.root)
    return {"arm": arm, "seed": seed, "n_candidates": len(cands), "median": float(np.median(cands)),
            "best": max(cands), "zero_shot": base["no_memory"], "fewshot_all": base["fewshot_all"],
            "n_above_zero_shot": sum(c > base["no_memory"] for c in cands),
            "selected": best, "selected_test": test.get(best, {}).get("score"),
            "trace_files_per_iter": ss.get("traces_per_iter", 0.0), "candidate_scores": cands}


def main():
    def extra(ap):
        ap.add_argument("--batch", default=None)
        ap.add_argument("--merge", action="store_true")
    args = parse_args(__doc__.splitlines()[0], default_seeds=20, extra=extra)
    part = lambda i: RESULTS / f"m8_history_ablation_n20.part{i}.json"   # noqa: E731
    if not args.merge:
        i, n = map(int, (args.batch or "0/1").split("/"))
        seeds = [s for s in range(args.seeds) if s % n == i]
        rows = pool_map(job, [(a, s) for s in seeds for a in ARMS], min(args.workers, 2))
        part(i).write_text(json.dumps(rows))
        print(f"[saved] {part(i)}")
        return
    rows = []
    for p in sorted(RESULTS.glob("m8_history_ablation_n20.part*.json")):
        rows += json.loads(p.read_text())
    by = {a: {r["seed"]: r for r in rows if r["arm"] == a} for a in ARMS}
    seeds = sorted(set.intersection(*[set(by[a]) for a in ARMS]))
    summ = {a: {k: summarize([by[a][s][k] for s in seeds]) for k in ("median", "best", "selected_test",
                                                                        "n_above_zero_shot")} for a in ARMS}
    cmp = {f"full_minus_{a}": {k: paired([by[a][s][k] for s in seeds], [by["full"][s][k] for s in seeds])
                               for k in ("median", "best", "selected_test")} for a in ("scores_only", "scores_summary")}
    cmp["summary_minus_scores_only"] = {k: paired([by["scores_only"][s][k] for s in seeds],
                                                  [by["scores_summary"][s][k] for s in seeds]) for k in ("median", "best")}
    beats = [by["full"][s]["median"] > max(by["scores_only"][s]["best"], by["scores_summary"][s]["best"]) for s in seeds]
    crit_i = all(cmp[f"full_minus_{a}"][k].get("lo", -1) > 0 for a in ("scores_only", "scores_summary")
                 for k in ("median", "best"))
    crit_ii = sum(beats) >= 0.8 * len(seeds)
    median_ok = all(cmp[f"full_minus_{a}"]["median"].get("lo", -1) > 0 for a in ("scores_only", "scores_summary"))
    mock_verdict = ("(i)+(ii) hold" if crit_i and crit_ii else "(i) holds, (ii) fails" if crit_i else
                    "(i) fails" + ("" if median_ok else " on the median comparisons"))
    print(table([[a, fmt(summ[a]["median"]), fmt(summ[a]["best"]), fmt(summ[a]["selected_test"])] for a in ARMS],
                ["arm", "median search", "best search", "selected test"]))
    print(json.dumps({k: {kk: {x: round(y, 4) for x, y in vv.items() if isinstance(y, float)} for kk, vv in v.items()}
                      for k, v in cmp.items()}))
    print(f"median full > best of both ablations in {sum(beats)}/{len(seeds)} seeds; mock criteria: {mock_verdict}")
    save("m8_history_ablation_n20", {
        "claim": "Q3 (Table 3) at the paper's scale: full traces beat scores-only and scores+summary; the median full "
                 "candidate beats the best of either ablation",
        "preregistration": "claims-audit-metaharness.md, Retry round 2: preregistration, P2",
        "config": {"seeds": seeds, "iterations": N_ITER, "k": K, "proposer": "MockProposer", "domain": "memoclassify"},
        "summary": summ, "paired": cmp, "median_full_beats_best_of_ablations_per_seed": beats,
        "n_seeds_median_full_beats_best": int(sum(beats)), "criterion_i": crit_i, "criterion_ii": crit_ii,
        "criterion_i_median_only": median_ok, "mock_criteria": mock_verdict, "per_seed": rows,
        "caveat": "Offline MockProposer: its trace diagnosis is richer than its summary diagnosis by construction; "
                  "Q3 can only be upgraded together with the live probe M9 (P3)."})


if __name__ == "__main__":
    main()
