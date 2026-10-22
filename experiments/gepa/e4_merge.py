"""E4: GEPA vs GEPA+Merge under soft, hard and accepted-only merge caps; gains of complementary vs overlapping merges.

Run: python experiments/gepa/e4_merge.py [--llm sim|claude:haiku] [--seeds N] [--quick]
"""
from __future__ import annotations

import functools

import numpy as np

from _common import (RESULTS, bar_plot, by_seed, fmt, fmt_diff, paired, parse_args, pool_map, reflection_llm, save,
                     summarize)
from rsi.domains.ruleworld import make_domain
from rsi.gepa import Config, run

ARMS = {"gepa": {}, "merge_soft5": {"use_merge": True},
        "merge_hard5": {"use_merge": True, "merge_cap_mode": "hard"},
        "merge_accepted5": {"use_merge": True, "merge_cap_mode": "accepted"},
        "merge_soft1": {"use_merge": True, "max_merge_invocations": 1},
        "merge_soft20": {"use_merge": True, "max_merge_invocations": 20}}


def job(spec):
    arm, seed, B, llm = spec
    d = make_domain(seed=seed)
    cfg = Config(max_metric_calls=B, seed=seed, **ARMS[arm])
    res = run(d, d.seed_artifact(), llm_propose=reflection_llm(llm, d.world), config=cfg)
    st = res.state
    truth = functools.cache(lambda k: d.expected(st.candidates[k], "test"))
    changed = lambda k, a: {c for c in st.components if st.candidates[k].get(c) != st.candidates[a].get(c)}
    merges = []
    for k, kind in enumerate(st.kinds):
        if kind != "merge":
            continue
        i, j = st.parents[k]
        a = res.ledger[f"c{k}"].meta["ancestor"]
        changed_i, changed_j = changed(i, a), changed(j, a)
        merges.append({"gain_vs_best_parent": truth(k) - max(truth(i), truth(j)),
                       "beats_both": truth(k) > max(truth(i), truth(j)),
                       "complementary": bool(changed_i - changed_j) and bool(changed_j - changed_i),
                       "rollouts": st.discovery_evals[k], "frac_budget": st.discovery_evals[k] / B})
    ev = [e for e in st.trace if e.get("invoked_merge")]
    n_rej = sum(1 for e in ev if e.get("event") == "merge_rejected")
    assert res.meta["n_merge_invocations"] == len(merges) + n_rej      # every invocation is accepted or rejected
    return {"arm": arm, "seed": seed, "B": B, "final_test": d.expected(res.best, "test"),
            "n_merges": len(merges), "n_merge_checks": len(ev), "n_merge_invocations": len(merges) + n_rej,
            "n_merge_rejected": n_rej,
            "merge_rollouts": st.counter.by_phase["merge_subsample"] + st.counter.by_phase["val_merge"],
            "best_is_merge": st.kinds[st.best_idx()] == "merge", "merges": merges,
            "cap": cfg.max_merge_invocations if cfg.use_merge else 0}


def merge_group(ms: list[dict]) -> dict:
    return {"n": len(ms), "gain": summarize([m["gain_vs_best_parent"] for m in ms]),
            "frac_beats_both": float(np.mean([m["beats_both"] for m in ms])) if ms else None}


def arm_summary(rs: list[dict], gepa: list[dict], is_gepa: bool, B: int) -> dict:
    ms = [m for r in rs for m in r["merges"]]
    gain = lambda sel: summarize([m["gain_vs_best_parent"] for m in ms if sel(m)])
    over_cap = lambda key: 0 if is_gepa else float(np.mean([r[key] > r["cap"] for r in rs]))
    return {"final_test": summarize([r["final_test"] for r in rs]),
            "vs_gepa": None if is_gepa else paired([r["final_test"] for r in gepa], [r["final_test"] for r in rs]),
            "merges_accepted": summarize([r["n_merges"] for r in rs]),
            "runs_exceeding_cap": over_cap("n_merges"),
            "runs_invocations_exceeding_cap": over_cap("n_merge_invocations"),
            "max_invocations_per_run": max(r["n_merge_invocations"] for r in rs),
            "merge_checks": summarize([r["n_merge_checks"] for r in rs]),
            "merge_invocations": summarize([r["n_merge_invocations"] for r in rs]),
            "merges_rejected": summarize([r["n_merge_rejected"] for r in rs]),
            "merge_rollout_share": summarize([r["merge_rollouts"] / B for r in rs]),
            "best_is_merge": float(np.mean([r["best_is_merge"] for r in rs])),
            "n_merges_total": len(ms),
            "merge_gain_vs_best_parent": gain(lambda m: True),
            "frac_beats_both_parents": float(np.mean([m["beats_both"] for m in ms])) if ms else None,
            "complementary": merge_group([m for m in ms if m["complementary"]]),
            "overlapping": merge_group([m for m in ms if not m["complementary"]]),
            "early_merges(<33% budget)": gain(lambda m: m["frac_budget"] < 0.33),
            "late_merges": gain(lambda m: m["frac_budget"] >= 0.33)}


def _pd(s: dict) -> str:
    v = s.get("vs_gepa") or {}
    return f"paired vs GEPA {fmt_diff(v)}" if v else "paired vs GEPA: -"


def budget_verdict(B: int, summ: dict) -> str:
    s5 = summ["merge_soft5"]
    parts = [f"[B={B}] GEPA {fmt(summ['gepa']['final_test'])} vs GEPA+Merge(soft 5, reference) "
             f"{fmt(s5['final_test'])} ({_pd(s5)})"]
    if "merge_hard5" in summ:
        h = summ["merge_hard5"]
        parts.append(f"hard invocation cap 5 (paper) {fmt(h['final_test'])} ({_pd(h)}; "
                     f"{h['merge_invocations']['mean']:.1f} invocations/run, max {h['max_invocations_per_run']}, "
                     f"{h['merges_accepted']['mean']:.1f} accepted, {h['merge_rollout_share']['mean']:.0%} of "
                     f"rollouts on merge)")
    if "merge_accepted5" in summ:
        h = summ["merge_accepted5"]
        parts.append(f"accepted-cap 5 (pre-audit 'hard') {fmt(h['final_test'])} ({_pd(h)}; "
                     f"{h['merge_invocations']['mean']:.1f} invocations/run, max {h['max_invocations_per_run']}, "
                     f"{h['merge_rollout_share']['mean']:.0%} of rollouts on merge)")
    txt = "; ".join(parts) + (
        f". Soft cap 5: accepted merges {s5['merges_accepted']['mean']:.1f}/run "
        f"({s5['runs_exceeding_cap']:.0%} of runs exceed the cap), invocations "
        f"{s5['merge_invocations']['mean']:.1f}/run ({s5['runs_invocations_exceeding_cap']:.0%} of runs exceed 5), "
        f"{s5['merge_rollout_share']['mean']:.0%} of rollouts on merge")
    if "merge_soft1" in summ:
        txt += (f"; soft cap 1 -> {summ['merge_soft1']['merges_accepted']['mean']:.1f} accepted/run, "
                f"{summ['merge_soft1']['runs_exceeding_cap']:.0%} exceed")
    comp, over = s5["complementary"], s5["overlapping"]
    return txt + (f". Complementary merges (soft 5) beat both parents {comp['frac_beats_both'] or 0:.0%} "
                  f"(mean gain {comp['gain']['mean']:+.3f}, n={comp['n']}) vs overlapping "
                  f"{over['frac_beats_both'] or 0:.0%} (gain {over['gain']['mean']:+.3f}, "
                  f"n={over['n']}); early merges gain {s5['early_merges(<33% budget)']['mean']:+.3f} vs "
                  f"late {s5['late_merges']['mean']:+.3f}.")


def main():
    a = parse_args("E4: merge", default_seeds=30)
    budgets = [400] if a.live else ([1500] if a.quick else [1500, 4000])
    arms = list(ARMS) if not a.live else ["gepa", "merge_soft5"]
    rows = pool_map(job, [(arm, s, B, a.llm) for B in budgets for arm in arms for s in range(a.seeds)], a.workers)
    out = {"experiment": "E4 merge", "llm": a.llm, "config": {"budgets": budgets, "seeds": a.seeds, "arms": ARMS},
           "summary": {}, "raw": rows}
    verdicts = []
    for B in budgets:
        by = {arm: by_seed(rows, arm=arm, B=B) for arm in arms}
        summ = out["summary"][f"B={B}"] = {arm: arm_summary(rs, by["gepa"], arm == "gepa", B)
                                           for arm, rs in by.items()}
        if "merge_soft5" in summ:
            verdicts.append(budget_verdict(B, summ))
    out["verdict"] = " ".join(verdicts)
    save("e4_merge", out, a.out)
    labels = [f"B={b}\n{arm}" for b in budgets for arm in arms]
    stats = [out["summary"][f"B={b}"][arm]["final_test"] for b in budgets for arm in arms]
    bar_plot(RESULTS / "e4_merge.png", labels, stats, "E4: GEPA vs GEPA+Merge", "true test score")
    print(out["verdict"])


if __name__ == "__main__":
    main()
