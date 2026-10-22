"""Retry round 2: GEPA claims re-tested in RuleWorld analogues of the paper's six benchmarks (modules, splits, budget).

Run: python experiments/gepa/r2_paper_regime.py [--seeds 50] [--rl-seeds 30] [--seed0 100] [--part all|rl|main]
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from _common import (RESULTS, NewestTieBest, by_seed, fmt_diff, paired, parse_args, pool_map, reflection_llm, save,
                     summarize)
from rsi.core.llm import estimate_tokens
from rsi.domains.ruleworld import make_domain
from rsi.gepa import Config, FewShotConfig, RLConfig, run, run_fewshot, run_scalar_rl, tree_metrics

SETTINGS = {
    "hotpotqa": dict(modules=("hop1", "summary1", "hop2", "answer"), scoring="binary", n_train=150, n_val=300, B=6871),
    "ifbench": dict(modules=("answer", "rewrite"), scoring="partial", n_train=150, n_val=300, B=3593),
    "hover": dict(modules=("hop1", "summary1", "hop2", "summary2"), scoring="binary", n_train=150, n_val=300, B=7051),
    "pupa": dict(modules=("redact", "respond"), scoring="partial", n_train=111, n_val=111, B=2426),
    "aime": dict(modules=("cot",), scoring="binary", n_train=45, n_val=45, B=1839),
    "livebench": dict(modules=("cot",), scoring="binary", n_train=123, n_val=123, B=1839),
}
TABLE3 = ["hotpotqa", "ifbench", "hover", "pupa"]
MODELS = {"gpt": {}, "qwen": dict(capacity=8, dilution=0.05, slip=0.15, p_demo=0.1)}
SEL_ARMS = {"pareto": "pareto", "current_best": "current_best", "beam4": "beam_search", "newest_tie": None}
MERGE_ARMS = {"merge_soft5": dict(use_merge=True), "merge_hard5": dict(use_merge=True, merge_cap_mode="hard"),
              "merge_hard5_late": dict(use_merge=True, merge_cap_mode="hard", merge_start_frac=0.5)}
RL_LRS = [0.5, 2.0, 8.0]
RL_BUDGET = 24000
SEED0 = 100


def domain(setting: str, model: str, seed: int):
    s = {k: v for k, v in SETTINGS[setting].items() if k != "B"}
    return make_domain(seed=seed, **s, **MODELS[model])


def prompt_tokens(art) -> int:
    return sum(estimate_tokens(art[c]) for c in art.files if c.startswith("prompts/"))


def job(spec):
    setting, model, arm, seed = spec
    d = domain(setting, model, seed)
    B = SETTINGS[setting]["B"]
    row = {"setting": setting, "model": model, "arm": arm, "seed": seed, "B": B,
           "seed_test": d.expected(d.seed_artifact(), "test"), "oracle_test": d.expected(d.oracle_artifact(), "test")}
    if arm.startswith("rl_lr="):
        res = run_scalar_rl(d, d.seed_artifact(),
                            config=RLConfig(max_metric_calls=RL_BUDGET, lr=float(arm[6:]), seed=seed))
        row.update(final_test=d.expected(res.best, "test"), best_val=res.meta["best_val"])
        return row
    if arm == "mipro_lite":
        res = run_fewshot(d, d.seed_artifact(), llm_propose=reflection_llm("sim", d.world),
                          config=FewShotConfig(max_metric_calls=B, seed=seed))
        row.update(final_test=d.expected(res.best, "test"), best_val=res.meta["best_val"],
                   tokens=prompt_tokens(res.best), rollouts=res.meta["rollouts"])
        return row
    kw = dict(MERGE_ARMS.get(arm, {}))
    sel = SEL_ARMS.get(arm, "pareto")
    custom = NewestTieBest() if arm == "newest_tie" else None
    merged = []                                   # every merged child built and scored (accepted or rejected)

    def cb(event, payload):
        if event not in ("merge_accepted", "merge_rejected"):
            return
        st, prop = payload["engine"].state, payload["proposal"]
        files = prop.candidate.files
        cands, (i, j), a = st.candidates, prop.parents, prop.ancestor
        ch_i = {c for c in st.components if cands[i].get(c) != cands[a].get(c)}
        ch_j = {c for c in st.components if cands[j].get(c) != cands[a].get(c)}
        if event == "merge_accepted":             # the child is already the last candidate
            degenerate = any(c.files == files for c in cands[:-1] if c is not None)
        else:
            degenerate = any(c.files == files for c in cands)
        merged.append({"accepted": event == "merge_accepted",
                       "prose_disjoint": bool(ch_i) and bool(ch_j) and not (ch_i & ch_j),
                       "degenerate": degenerate, "rollouts": st.counter.total})

    res = run(d, d.seed_artifact(), llm_propose=reflection_llm("sim", d.world), selector=custom, callbacks=[cb],
              config=Config(max_metric_calls=B, seed=seed,
                            candidate_selection=sel if custom is None else "current_best", **kw))
    st = res.state
    best = st.best_idx()
    iters = sum(1 for e in st.trace if "iteration_id" in e)
    tm = tree_metrics(st)
    row.update(final_test=d.expected(res.best, "test"), best_val=res.meta["best_val"], iterations=iters,
               n_reflective_accepted=sum(1 for e in st.trace if e.get("event") == "accepted"),
               best_discovery_rollouts=int(st.discovery_evals[best]),
               stalled_frac=(iters - st.discovery_iter[best]) / max(iters, 1) if st.discovery_iter[best] >= 0 else 1.0,
               tokens=prompt_tokens(res.best), rollouts=st.counter.total, **tm)
    if kw:
        row.update(n_merge_checks=sum(1 for e in st.trace if e.get("invoked_merge")),
                   n_merge_invocations=int(res.meta["n_merge_invocations"]),
                   n_merge_accepted=sum(1 for k in st.kinds if k == "merge"),
                   n_merge_degenerate=sum(m["degenerate"] for m in merged),
                   n_merge_prose_disjoint=sum(m["prose_disjoint"] for m in merged),
                   first_merge_rollouts=min([m["rollouts"] for m in merged], default=None),
                   merge_rollout_share=(st.counter.by_phase["merge_subsample"] + st.counter.by_phase["val_merge"]) /
                   max(st.counter.total, 1))
    if arm == "pareto" and model == "qwen":        # L10: the prompt optimized on the weak model, run on the strong one
        g = domain(setting, "gpt", seed)
        row.update(transfer_test_gpt=g.expected(res.best, "test"), seed_test_gpt=g.expected(g.seed_artifact(), "test"))
    return row


def jobs(n_seeds: int, n_rl_seeds: int, seed0: int = SEED0) -> list:
    out = []
    for s in range(seed0, seed0 + n_seeds):
        for st in SETTINGS:
            arms = ["pareto", *MERGE_ARMS, "mipro_lite"] + (["current_best", "beam4", "newest_tie"]
                                                              if st in TABLE3 else [])
            out += [(st, m, a, s) for m in MODELS for a in arms]
    out += [(st, "qwen", f"rl_lr={lr}", s) for s in range(seed0, seed0 + n_rl_seeds) for st in SETTINGS
            for lr in RL_LRS]
    return out


def _vals(rows, key, **f) -> dict:
    return {r["seed"]: r[key] for r in by_seed(rows, **f)}


def aggregate_paired(rows, arm_a, arm_b, settings, model, key="final_test") -> dict:
    """Paired (b - a) of the per-seed mean over ``settings`` (the paper's 'aggregate' column) and per setting."""
    per, vals_a, vals_b = {}, {}, {}
    seeds = None
    for st in settings:
        a = vals_a[st] = _vals(rows, key, setting=st, model=model, arm=arm_a)
        b = vals_b[st] = _vals(rows, key, setting=st, model=model, arm=arm_b)
        common = sorted(set(a) & set(b))
        per[st] = paired([a[s] for s in common], [b[s] for s in common])
        seeds = set(common) if seeds is None else seeds & set(common)
    seeds = sorted(seeds or [])
    agg_a = [float(np.mean([vals_a[st][s] for st in settings])) for s in seeds]
    agg_b = [float(np.mean([vals_b[st][s] for st in settings])) for s in seeds]
    return {"aggregate": paired(agg_a, agg_b), "aggregate_a": summarize(agg_a), "aggregate_b": summarize(agg_b),
            "per_setting": per}


def best_rl_arm(rows, setting):
    return max((f"rl_lr={lr}" for lr in RL_LRS),
               key=lambda a: np.mean([r["best_val"] for r in by_seed(rows, setting=setting, model="qwen", arm=a)]
                                     or [-1]))


def analyse_selection(rows, m: str, A: dict) -> None:
    """L3 / L4 / L12 (Table 3 analogue)."""
    l3 = aggregate_paired(rows, "current_best", "pareto", TABLE3, m)
    wins = sum(v["mean_diff"] > 0 for v in l3["per_setting"].values())
    sig_loss = [k for k, v in l3["per_setting"].items() if v["hi"] < 0]
    ctrl = aggregate_paired(rows, "newest_tie", "pareto", TABLE3, m)
    A[f"L3[{m}]"] = {**l3, "settings_pareto_ahead": wins, "settings_current_best_significantly_ahead": sig_loss,
                     "control_pareto_minus_newest_tie": ctrl,
                     "pass": bool(l3["aggregate"]["lo"] > 0 and wins >= 3 and not sig_loss)}
    l4a = aggregate_paired(rows, "beam4", "current_best", TABLE3, m)
    l4b = aggregate_paired(rows, "beam4", "pareto", TABLE3, m)
    A[f"L4[{m}]"] = {"current_best_minus_beam": l4a, "pareto_minus_beam": l4b,
                     "pass": bool(l4a["aggregate"]["lo"] > 0 and l4b["aggregate"]["lo"] > 0)}
    per = {}
    for st in TABLE3:
        cb = by_seed(rows, setting=st, model=m, arm="current_best")
        pareto = by_seed(rows, setting=st, model=m, arm="pareto")
        dp = aggregate_paired(rows, "current_best", "pareto", [st], m, key="distinct_parents")["aggregate"]
        per[st] = {"current_best_stalled_frac": summarize([r["stalled_frac"] for r in cb]),
                   "current_best_max_depth_median": float(np.median([r["max_depth"] for r in cb])),
                   "pareto_max_depth_median": float(np.median([r["max_depth"] for r in pareto])),
                   "distinct_parents_pareto_minus_current_best": dp,
                   "pass_stall": bool(np.mean([r["stalled_frac"] for r in cb]) >= 0.5),
                   "pass_balance": bool(dp["lo"] > 0)}
    n_ok = sum(v["pass_stall"] and v["pass_balance"] for v in per.values())
    A[f"L12[{m}]"] = {"per_setting": per, "settings_passing_both": n_ok,
                      "verdict": "REPRODUCED" if n_ok == len(TABLE3) else ("PARTIAL" if n_ok else "NOT REPRODUCED")}


def analyse_merge_sparsity(rows, cells) -> dict:
    """L6: how rarely merge fires under the reference schedule (all 12 cells)."""
    per = {}
    all_rate, all_checks, all_inv = [], 0, 0
    for st, m in cells:
        rs = by_seed(rows, setting=st, model=m, arm="merge_soft5")
        col = lambda k: [r[k] for r in rs]
        rate = [r["n_merge_invocations"] / max(r["iterations"], 1) for r in rs]
        chk, inv = sum(col("n_merge_checks")), sum(col("n_merge_invocations"))
        all_rate += rate
        all_checks += chk
        all_inv += inv
        per[f"{st}/{m}"] = {
            "invocations_per_iteration": summarize(rate), "invocations_per_run": summarize(col("n_merge_invocations")),
            "checks_per_run": summarize(col("n_merge_checks")), "accepted_per_run": summarize(col("n_merge_accepted")),
            "degenerate_per_run": summarize(col("n_merge_degenerate")),
            "prose_disjoint_per_run": summarize(col("n_merge_prose_disjoint")),
            "no_triplet_fraction_of_checks": (chk - inv) / chk if chk else None,
            "iterations_per_run": summarize(col("iterations")),
            "merge_rollout_share": summarize(col("merge_rollout_share")),
            "runs_over_5_invocations": float(np.mean([r["n_merge_invocations"] > 5 for r in rs])),
            "sparse": bool(np.mean(rate) <= 0.10)}
    pooled_rate = float(np.mean(all_rate))
    no_trip = (all_checks - all_inv) / all_checks if all_checks else None
    n_sparse = sum(v["sparse"] for v in per.values())
    s1, s2 = pooled_rate <= 0.10, (no_trip or 0) >= 0.5
    return {"per_cell": per, "pooled_invocations_per_iteration": pooled_rate,
            "pooled_invocations_per_iteration_ci": summarize(all_rate), "pooled_no_triplet_fraction": no_trip,
            "cells_sparse": n_sparse, "S1_pooled_rate_le_0.10": s1, "S2_no_triplet_ge_0.5": s2,
            "verdict": "REPRODUCED" if s1 and s2 and n_sparse >= 10 else ("CONTRADICTED" if not s1 else "PARTIAL")}


def analyse_vs_mipro(rows, cells, A: dict) -> None:
    """Q10 / Q13 / Q12 analogue: GEPA vs MIPRO-lite score and prompt length in all 12 cells."""
    q10, q13 = {}, {}
    for st, m in cells:
        g = _vals(rows, "final_test", setting=st, model=m, arm="pareto")
        mp = _vals(rows, "final_test", setting=st, model=m, arm="mipro_lite")
        common = sorted(set(g) & set(mp))
        q10[f"{st}/{m}"] = paired([mp[s] for s in common], [g[s] for s in common])
        gt = _vals(rows, "tokens", setting=st, model=m, arm="pareto")
        mt = _vals(rows, "tokens", setting=st, model=m, arm="mipro_lite")
        rat = [mt[s] / gt[s] for s in common if gt[s]]
        mean_mt, mean_gt = np.mean([mt[s] for s in common]), np.mean([gt[s] for s in common])
        q13[f"{st}/{m}"] = {"median_ratio": float(np.median(rat)), "max_ratio": float(np.max(rat)),
                            "ratio_of_means": float(mean_mt / mean_gt)}
    all_rat = [v["median_ratio"] for v in q13.values()]
    max_ratio = max(v["max_ratio"] for v in q13.values())
    A["Q10"] = {"per_cell": q10, "cells_gepa_significantly_ahead": sum(v["lo"] > 0 for v in q10.values()),
                "cells_mipro_significantly_ahead": [k for k, v in q10.items() if v["hi"] < 0],
                "max_cell_gain": max(v["mean_diff"] for v in q10.values()),
                "pass_every_setting": all(v["lo"] > 0 for v in q10.values())}
    A["Q13"] = {"per_cell": q13, "median_of_cell_medians": float(np.median(all_rat)),
                "max_per_seed_ratio": max_ratio,
                "pass_33pct_shorter(ratio>=1.5)": bool(np.median(all_rat) >= 1.5),
                "pass_less_than_33pct_of_size(ratio>=3)": bool(np.median(all_rat) >= 3.0),
                "pass_up_to_9.2x": bool(max_ratio >= 9.2)}
    gain = lambda m, arm: np.mean([r["final_test"] - r["seed_test"] for st in SETTINGS
                                   for r in by_seed(rows, setting=st, model=m, arm=arm)])
    q12 = {}
    for m in MODELS:
        gg, mg = gain(m, "pareto"), gain(m, "mipro_lite")
        q12[m] = {"gepa_gain": float(gg), "mipro_lite_gain": float(mg), "ratio": float(gg / mg) if mg else None}
    A["Q12_analogue"] = q12


def analyse_vs_rl(rows, A: dict) -> None:
    """L2 / Q7 (qwen, Table 1): GEPA at the paper's budget vs ScalarRL at 24000 rollouts."""
    per, ga, ra = {}, {}, {}
    for st in SETTINGS:
        arm = best_rl_arm(rows, st)
        g = ga[st] = _vals(rows, "final_test", setting=st, model="qwen", arm="pareto")
        rl = ra[st] = _vals(rows, "final_test", setting=st, model="qwen", arm=arm)
        common = sorted(set(g) & set(rl))
        per[st] = {"rl_arm": arm, **paired([rl[s] for s in common], [g[s] for s in common])}
    seeds = sorted(set.intersection(*[set(ga[st]) & set(ra[st]) for st in SETTINGS]))
    agg = paired([np.mean([ra[st][s] for st in SETTINGS]) for s in seeds],
                 [np.mean([ga[st][s] for st in SETTINGS]) for s in seeds])
    wins = sum(v["mean_diff"] > 0 for v in per.values())
    A["L2"] = {"per_setting": per, "aggregate_gepa_minus_rl": agg, "settings_gepa_ahead": wins,
               "pass": bool(agg["lo"] > 0 and wins >= 5), "rl_significantly_ahead": bool(agg["hi"] < 0)}
    q7 = {}
    for st in SETTINGS:
        disc = [r["best_discovery_rollouts"] for r in by_seed(rows, setting=st, model="qwen", arm="pareto")]
        ratio = [RL_BUDGET / max(x, 1) for x in disc]
        q7[st] = {"median_ratio": float(np.median(ratio)), "ratio": summarize(ratio),
                  "median_rollouts_to_best": float(np.median(disc))}
    A["Q7"] = {"per_setting": q7, "pass_all_medians_in_4_35": all(4 <= v["median_ratio"] <= 35 for v in q7.values())}


def analyse_transfer(rows) -> dict:
    """L10: the prompt optimized on the weak model (qwen), run on the strong one (gpt)."""
    vals = lambda key, model, arm: {st: _vals(rows, key, setting=st, model=model, arm=arm) for st in SETTINGS}
    transfer = vals("transfer_test_gpt", "qwen", "pareto")
    seeds = sorted(set.intersection(*[set(transfer[st]) for st in SETTINGS]))
    mean = lambda v: [np.mean([v[st][s] for st in SETTINGS]) for s in seeds]
    tr, sg = mean(transfer), mean(vals("seed_test_gpt", "qwen", "pareto"))
    gpt_direct, dm = mean(vals("final_test", "gpt", "pareto")), mean(vals("final_test", "gpt", "mipro_lite"))
    gain_tr = paired(sg, tr)
    vs_mipro = paired(dm, tr)
    return {"transferred_gain_over_seed_on_strong": gain_tr, "transferred_minus_mipro_lite_direct_on_strong": vs_mipro,
            "transferred_minus_gepa_direct_on_strong": paired(gpt_direct, tr),
            "retention": float((np.mean(tr) - np.mean(sg)) / (np.mean(gpt_direct) - np.mean(sg))),
            "pass": bool(gain_tr["lo"] > 0 and vs_mipro["lo"] > 0)}


def analyse(rows) -> dict:
    A: dict = {}
    cells = [(st, m) for st in SETTINGS for m in MODELS]
    for m in MODELS:
        analyse_selection(rows, m, A)
    A["L6"] = analyse_merge_sparsity(rows, cells)
    for m in MODELS:                              # L13 (+ Q14 / L5 context): merge allocation and timing
        alloc = aggregate_paired(rows, "merge_soft5", "merge_hard5", list(SETTINGS), m)
        timing = aggregate_paired(rows, "merge_hard5", "merge_hard5_late", list(SETTINGS), m)
        degr = {a: aggregate_paired(rows, "pareto", a, list(SETTINGS), m) for a in MERGE_ARMS}
        ta, tt = alloc["aggregate"]["lo"] > 0, timing["aggregate"]["lo"] > 0
        A[f"L13[{m}]"] = {"hard5_minus_soft5": alloc, "late_minus_early_hard5": timing, "merge_minus_gepa": degr,
                          "T_alloc": bool(ta), "T_timing": bool(tt),
                          "verdict": "REPRODUCED" if ta and tt else ("PARTIAL" if ta or tt else "NOT REPRODUCED")}
    analyse_vs_mipro(rows, cells, A)
    if any(r["arm"].startswith("rl_lr=") for r in rows):
        analyse_vs_rl(rows, A)
    A["L10"] = analyse_transfer(rows)
    return A


def verdict_text(A: dict) -> str:
    out = []
    for m in ("qwen", "gpt"):
        l3, l4, l12, l13 = (A[f"{c}[{m}]"] for c in ("L3", "L4", "L12", "L13"))
        out.append(f"L3[{m}] Pareto - CurrentBest aggregate {fmt_diff(l3['aggregate'])}, ahead in "
                   f"{l3['settings_pareto_ahead']}/4 -> pass={l3['pass']}")
        out.append(f"L4[{m}] CurrentBest - Beam {fmt_diff(l4['current_best_minus_beam']['aggregate'])}, Pareto - Beam "
                   f"{fmt_diff(l4['pareto_minus_beam']['aggregate'])} -> pass={l4['pass']}")
        out.append(f"L12[{m}] {l12['settings_passing_both']}/4 settings pass -> {l12['verdict']}")
        out.append(f"L13[{m}] hard5 - soft5 {fmt_diff(l13['hard5_minus_soft5']['aggregate'])}; late - early "
                   f"{fmt_diff(l13['late_minus_early_hard5']['aggregate'])} -> {l13['verdict']}")
    l6 = A["L6"]
    out.append(f"L6 pooled merge invocations per iteration {l6['pooled_invocations_per_iteration']:.3f}, no-triplet "
               f"fraction {l6['pooled_no_triplet_fraction']:.2f}, sparse cells {l6['cells_sparse']}/12 -> "
               f"{l6['verdict']}")
    out.append(f"Q10 GEPA significantly ahead of MIPRO-lite in {A['Q10']['cells_gepa_significantly_ahead']}/12 cells")
    out.append(f"Q13 median MIPRO/GEPA token ratio {A['Q13']['median_of_cell_medians']:.2f}x, "
               f"max {A['Q13']['max_per_seed_ratio']:.1f}x")
    if "L2" in A:
        out.append(f"L2 GEPA - RL(24k) aggregate {fmt_diff(A['L2']['aggregate_gepa_minus_rl'])}, ahead in "
                   f"{A['L2']['settings_gepa_ahead']}/6 -> pass={A['L2']['pass']}")
        out.append("Q7 median 24000/rollouts-to-best: " + ", ".join(f"{k} {v['median_ratio']:.1f}x"
                                                                    for k, v in A["Q7"]["per_setting"].items()))
    l10 = A["L10"]
    out.append(f"L10 weak->strong transferred gain {fmt_diff(l10['transferred_gain_over_seed_on_strong'])}, minus "
               f"MIPRO-lite direct {fmt_diff(l10['transferred_minus_mipro_lite_direct_on_strong'])}, "
               f"retention {l10['retention']:.2f}")
    return "; ".join(out)


def main():
    def extra(ap):
        ap.add_argument("--rl-seeds", type=int, default=30)
        ap.add_argument("--seed0", type=int, default=SEED0)
        ap.add_argument("--part", choices=("all", "rl", "main"), default="all",
                        help="rl: only the ScalarRL arms -> <out>_rl.json; main: the rest, merged with the rl file")

    a = parse_args("retry-2 paper-regime GEPA experiments", default_seeds=50, extra=extra)
    out_path = Path(a.out) if a.out else RESULTS / "r2_paper_regime.json"
    rl_path = out_path.with_name(out_path.stem + "_rl.json")
    n_rl = min(a.rl_seeds, a.seeds)
    all_jobs = jobs(a.seeds, n_rl, a.seed0)
    is_rl = lambda j: j[2].startswith("rl_lr=")
    if a.part == "rl":
        rows = pool_map(job, [j for j in all_jobs if is_rl(j)], a.workers)
        save("r2_paper_regime_rl", {"experiment": "retry-2 paper-matched regime, ScalarRL arms", "raw": rows},
             str(rl_path))
        return
    if a.part == "main":
        rows = pool_map(job, [j for j in all_jobs if not is_rl(j)], a.workers)
        if rl_path.exists():
            rows += json.loads(rl_path.read_text())["raw"]
    else:
        rows = pool_map(job, all_jobs, a.workers)
    A = analyse(rows)
    out = {"experiment": "retry-2 paper-matched regime", "llm": a.llm,
           "config": {"settings": SETTINGS, "models": MODELS, "seeds": [a.seed0, a.seed0 + a.seeds - 1],
                      "rl_seeds": [a.seed0, a.seed0 + n_rl - 1], "rl_budget": RL_BUDGET,
                      "rl_lrs": RL_LRS, "merge_arms": MERGE_ARMS},
           "analysis": A, "verdict": verdict_text(A), "raw": rows}
    save("r2_paper_regime", out, str(out_path))
    print(out["verdict"])


if __name__ == "__main__":
    main()
