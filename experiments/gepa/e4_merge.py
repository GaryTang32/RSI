"""E4 - Merge: does system-aware crossover combine complementary lessons?

Claims [paper App.D.1, Obs.5][doc "combine complementary lessons"]: GEPA+Merge can beat
GEPA (up to +5 pp) but not always (it hurt Qwen3-8B, attributed to budget allocation and
merge timing); in the reference code the "max 5 merges" cap is soft [run:merge-cap-probe].
Spec E4 expects merged children to beat both parents when lineages are module-disjoint,
the gain to vanish or turn negative when merges fire early or lineages overlap, and
accepted merges to exceed the cap under reference_soft.

RuleWorld default world (2 modules whose lessons are independent), arms: GEPA,
+Merge (reference soft cap 5), +Merge hard cap 5, +Merge soft cap 1, +Merge soft cap 20,
at B in {1500, 4000}. For every accepted merge: true test of the child vs both parents,
whether the two parents contributed different modules (complementary) or the same one
(overlapping), and when it fired (rollouts).

    python experiments/gepa/e4_merge.py [--llm sim|claude:haiku] [--seeds N] [--quick]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import RESULTS, bar_plot, fmt, paired, parse_args, pool_map, reflection_llm, save, summarize  # noqa

import numpy as np  # noqa: E402

from rsi.domains.ruleworld import make_domain  # noqa: E402
from rsi.gepa import Config, run  # noqa: E402

ARMS = {"gepa": {}, "merge_soft5": {"use_merge": True},
        "merge_hard5": {"use_merge": True, "merge_cap_mode": "hard"},
        "merge_soft1": {"use_merge": True, "max_merge_invocations": 1},
        "merge_soft20": {"use_merge": True, "max_merge_invocations": 20}}
ARGS = None


def job(spec):
    arm, seed, B = spec
    d = make_domain(seed=seed)
    cfg = Config(max_metric_calls=B, seed=seed, **ARMS[arm])
    res = run(d, d.seed_artifact(), llm_propose=reflection_llm(ARGS.llm, d.world), config=cfg)
    st = res.state
    t = {}

    def truth(k):
        if k not in t:
            t[k] = d.expected(st.candidates[k], "test")
        return t[k]

    merges = []
    for k, kind in enumerate(st.kinds):
        if kind != "merge":
            continue
        i, j = st.parents[k]
        node = res.ledger[f"c{k}"]
        a = node.meta["ancestor"]
        comps = st.components
        changed_i = {c for c in comps if st.candidates[i].get(c) != st.candidates[a].get(c)}
        changed_j = {c for c in comps if st.candidates[j].get(c) != st.candidates[a].get(c)}
        complementary = bool(changed_i - changed_j) and bool(changed_j - changed_i)
        merges.append({"gain_vs_best_parent": truth(k) - max(truth(i), truth(j)),
                       "beats_both": truth(k) > max(truth(i), truth(j)), "complementary": complementary,
                       "rollouts": st.discovery_evals[k], "frac_budget": st.discovery_evals[k] / B})
    ev = [e for e in st.trace if e.get("invoked_merge")]
    return {"arm": arm, "seed": seed, "B": B, "final_test": d.expected(res.best, "test"),
            "n_merges": len(merges), "n_merge_attempts": len(ev),
            "n_merge_rejected": sum(1 for e in ev if e.get("event") == "merge_rejected"),
            "merge_rollouts": st.counter.by_phase["merge_subsample"] + st.counter.by_phase["val_merge"],
            "best_is_merge": st.kinds[st.best_idx()] == "merge", "merges": merges,
            "cap": cfg.max_merge_invocations if cfg.use_merge else 0}


def main():
    global ARGS
    ARGS = a = parse_args("E4: merge", default_seeds=30)
    budgets = [400] if a.live else ([1500] if a.quick else [1500, 4000])
    arms = list(ARMS) if not a.live else ["gepa", "merge_soft5"]
    rows = pool_map(job, [(arm, s, B) for B in budgets for arm in arms for s in range(a.seeds)], a.workers)
    out = {"experiment": "E4 merge", "llm": a.llm, "config": {"budgets": budgets, "seeds": a.seeds, "arms": ARMS},
           "summary": {}, "raw": rows}
    verdicts = []
    for B in budgets:
        by = {arm: sorted([r for r in rows if r["arm"] == arm and r["B"] == B], key=lambda r: r["seed"])
              for arm in arms}
        summ = {}
        for arm, rs in by.items():
            ms = [m for r in rs for m in r["merges"]]
            comp = [m for m in ms if m["complementary"]]
            over = [m for m in ms if not m["complementary"]]
            early = [m for m in ms if m["frac_budget"] < 0.33]
            late = [m for m in ms if m["frac_budget"] >= 0.33]
            summ[arm] = {
                "final_test": summarize([r["final_test"] for r in rs]),
                "vs_gepa": paired([r["final_test"] for r in by["gepa"]], [r["final_test"] for r in rs])
                if arm != "gepa" else None,
                "merges_accepted": summarize([r["n_merges"] for r in rs]),
                "runs_exceeding_cap": float(np.mean([r["n_merges"] > r["cap"] for r in rs])) if arm != "gepa" else 0,
                "merge_attempts": summarize([r["n_merge_attempts"] for r in rs]),
                "merge_rollout_share": summarize([r["merge_rollouts"] / B for r in rs]),
                "best_is_merge": float(np.mean([r["best_is_merge"] for r in rs])),
                "n_merges_total": len(ms),
                "merge_gain_vs_best_parent": summarize([m["gain_vs_best_parent"] for m in ms]),
                "frac_beats_both_parents": float(np.mean([m["beats_both"] for m in ms])) if ms else None,
                "complementary": {"n": len(comp), "gain": summarize([m["gain_vs_best_parent"] for m in comp]),
                                  "frac_beats_both": float(np.mean([m["beats_both"] for m in comp])) if comp else None},
                "overlapping": {"n": len(over), "gain": summarize([m["gain_vs_best_parent"] for m in over]),
                                "frac_beats_both": float(np.mean([m["beats_both"] for m in over])) if over else None},
                "early_merges(<33% budget)": summarize([m["gain_vs_best_parent"] for m in early]),
                "late_merges": summarize([m["gain_vs_best_parent"] for m in late]),
            }
        out["summary"][f"B={B}"] = summ
        if "merge_soft5" in summ:
            s5 = summ["merge_soft5"]
            verdicts.append(
                f"[B={B}] GEPA {fmt(summ['gepa']['final_test'])} vs GEPA+Merge(soft 5) {fmt(s5['final_test'])} "
                f"(paired {s5['vs_gepa']['mean_diff']:+.3f} [{s5['vs_gepa']['lo']:+.3f}, {s5['vs_gepa']['hi']:+.3f}]); "
                f"hard cap {fmt(summ['merge_hard5']['final_test']) if 'merge_hard5' in summ else '-'}. "
                f"Accepted merges {s5['merges_accepted']['mean']:.1f}/run with cap 5 "
                f"({s5['runs_exceeding_cap']:.0%} of runs exceed the cap)"
                + (f"; cap 1 -> {summ['merge_soft1']['merges_accepted']['mean']:.1f}/run, "
                   f"{summ['merge_soft1']['runs_exceeding_cap']:.0%} exceed" if "merge_soft1" in summ else "")
                + f". Complementary merges beat both parents {s5['complementary']['frac_beats_both'] or 0:.0%} "
                f"(mean gain {s5['complementary']['gain']['mean']:+.3f}, n={s5['complementary']['n']}) vs overlapping "
                f"{s5['overlapping']['frac_beats_both'] or 0:.0%} (gain {s5['overlapping']['gain']['mean']:+.3f}, "
                f"n={s5['overlapping']['n']}); early merges gain {s5['early_merges(<33% budget)']['mean']:+.3f} vs late "
                f"{s5['late_merges']['mean']:+.3f}.")
    out["verdict"] = " ".join(verdicts)
    save("e4_merge", out, a.out)
    B = budgets[-1]
    labels = [f"B={b}\n{arm}" for b in budgets for arm in arms]
    stats = [out["summary"][f"B={b}"][arm]["final_test"] for b in budgets for arm in arms]
    bar_plot(RESULTS / "e4_merge.png", labels, stats, "E4: GEPA vs GEPA+Merge", "true test score")
    print(out["verdict"])


if __name__ == "__main__":
    main()
