"""M1 - Meta-Harness proposer-interface ablation: scores only vs scores + summaries vs full traces.

    python experiments/metaharness-solpi/m1_history_ablation.py [--llm sim|claude:haiku] [--seeds N] [--quick]
                                                               [--budget-hint N]
"""
from __future__ import annotations

import sys
from functools import partial
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import (figure_path, fmt, fresh_dir, live_llm, paired, parse_args, plt, pool_map, save,  # noqa: E402
                     summarize, table)
from mh_common import drop_traces, session_stats  # noqa: E402

import numpy as np  # noqa: E402

from rsi.domains.memoclassify import make_domain  # noqa: E402
from rsi.metaharness import Config, MemoClassifyLibrary, MockProposer, run  # noqa: E402

ARMS = ["scores_only", "scores_summary", "full"]
ABLATIONS = ("scores_only", "scores_summary")
SUMMARY_KEYS = ("median", "best", "n_above_zero_shot", "n_above_fewshot_all", "selected_search", "selected_test",
                "files_read_per_iter", "files_scanned_per_iter", "trace_files_per_iter", "trace_candidates_per_iter",
                "read_chars_per_iter", "view_chars_per_iter", "trace_chars_per_evaluation", "proposer_tokens")
CAVEAT = ("Offline, the proposer is a deterministic MockProposer whose diagnosis rules are written by us; "
          "the result shows the history views gate the information channel as intended, not that an LLM "
          "proposer benefits the same way. The mock diagnoses only its parents' traces (Markovian), and its "
          "long-prompt prior is tuned to the simulator unless --budget-hint is given. files_read counts what "
          "a proposal was based on; files_scanned what the mock only parsed; neither is the paper's "
          "tool-call count.")


def n_iterations(args) -> int:
    return 2 if args.live else (4 if args.quick else 8)


def job(args, spec) -> dict:
    arm, seed = spec
    dom = make_domain(seed=seed, scale=0.5 if args.live else 1.0)
    llm_p = live_llm(args.llm) if args.live else None
    prop = MockProposer(MemoClassifyLibrary(budget_hint=args.budget_hint), seed=seed) \
        if args.budget_hint and not args.live else None
    res = run(dom, dom.seed_artifact("fewshot_all"), llm_task=dom.make_model("A"), llm_propose=llm_p,
              config=Config(iterations=n_iterations(args), k=2, history_mode=arm, seed=seed,
                            validate_in_subprocess=True),
              out_dir=fresh_dir("m1", f"{arm}_s{seed}_{args.llm.replace(':', '_')}_h{args.budget_hint or 0}"),
              baselines=dom.baselines(), proposer=prop)
    st = res.loop.store
    names = st.names()
    base = {n: st.scores(n)["score"] for n in dom.baselines()}
    cands = [st.scores(n)["score"] for n in names if n not in base and st.scores(n)]
    sessions = res.trajectory
    ss = session_stats(st)
    best = res.meta["best_system"]
    test = res.meta["final"]["splits"]["test"]["results"]
    trace_chars = [sum(len(t) for t in st.traces(n).values()) for n in names]
    out = {"arm": arm, "seed": seed, "n_candidates": len(cands), "median": float(np.median(cands)) if cands else None,
           "best": max(cands) if cands else None, "zero_shot": base["no_memory"], "fewshot_all": base["fewshot_all"],
           "n_above_zero_shot": sum(c > base["no_memory"] for c in cands),
           "n_above_fewshot_all": sum(c > base["fewshot_all"] for c in cands),
           "selected": best, "selected_search": res.meta["frontier"]["_best"]["score"],
           "selected_test": test.get(best, {}).get("score"), "fewshot_all_test": test["fewshot_all"]["score"],
           "files_read_per_iter": ss.get("read_per_iter", 0.0),
           "files_scanned_per_iter": ss.get("scanned_per_iter", 0.0),
           "trace_files_per_iter": ss.get("traces_per_iter", 0.0),
           "trace_candidates_per_iter": ss.get("trace_cands_per_iter", 0.0),
           "view_chars_per_iter": float(np.mean([r["view_chars"] for r in sessions])),
           "read_chars_per_iter": float(np.mean([r["read_chars"] for r in sessions])),
           "trace_chars_per_evaluation": float(np.mean(trace_chars)) if trace_chars else 0.0,
           "reports_written": int(sum(r.get("reports_written", 0) for r in sessions)),
           "proposer_tokens": float(sum(r["proposer_tokens"] for r in sessions)),
           "proposer_usd": float(sum(r["proposer_usd"] for r in sessions)),
           "candidate_scores": cands}
    drop_traces(st.root)
    return out


def make_verdict(cmp: dict, n_beats: int, n_seeds: int) -> str:
    """REPRODUCED needs full > both ablations (median CI, best mean) AND summaries no better than scores-only."""
    full_wins = all(cmp[f"full_minus_{a}"]["median"].get("mean_diff", 0) > 0 and
                    cmp[f"full_minus_{a}"]["best"].get("mean_diff", 0) >= 0 for a in ABLATIONS)
    ci_excl = all(cmp[f"full_minus_{a}"]["median"].get("lo", -1) > 0 for a in ABLATIONS)
    best_sig = all(cmp[f"full_minus_{a}"]["best"].get("lo", -1) > 0 for a in ABLATIONS)
    summ_no_better = cmp["summary_minus_scores_only"]["median"].get("lo", 0) <= 0
    status = "REPRODUCED" if full_wins and ci_excl and summ_no_better else "PARTIAL" if full_wins else "NOT REPRODUCED"
    if full_wins and ci_excl:
        how = ": full history beats both ablations on median (CI excludes 0) and on best (mean)"
    elif full_wins:
        how = ": full history is ahead on average but not significantly on every comparison"
    else:
        how = ": full history does not beat the ablations"
    return (status + how
            + ("" if best_sig else " (best-candidate CI touches or includes 0 in at least one comparison)")
            + ("; summaries no better than scores-only (CI includes 0 or below), as in the paper" if summ_no_better
               else "; sub-claim NOT reproduced: summaries DO help over scores-only here (paper: they do not)")
            + f"; median full candidate > best of both ablations in {n_beats}/{n_seeds} seeds (paper: always)")


def main():
    args = parse_args(__doc__.splitlines()[0], extra=lambda ap: ap.add_argument(
        "--budget-hint", type=int, default=None, help="mock long-prompt prior in chars (default 11000, tuned)"))
    rows = pool_map(partial(job, args), [(a, s) for s in range(args.seeds) for a in ARMS], args.workers)
    by = {a: sorted([r for r in rows if r["arm"] == a], key=lambda r: r["seed"]) for a in ARMS}
    summ = {a: {k: summarize([r[k] for r in by[a]]) for k in SUMMARY_KEYS} for a in ARMS}
    # Table 3's strongest sub-claim, per seed: the median full candidate beats the best of either ablation
    by_seed = {a: {r["seed"]: r for r in by[a]} for a in ARMS}
    seeds = sorted(by_seed["full"])
    beats = [by_seed["full"][s]["median"] > max(by_seed[a][s]["best"] for a in ABLATIONS) for s in seeds]
    summ["full"]["n_seeds_median_full_beats_best_of_ablations"] = int(sum(beats))
    summ["full"]["n_seeds"] = len(seeds)
    cmp = {f"full_minus_{a}": {k: paired([r[k] for r in by[a]], [r[k] for r in by["full"]])
                               for k in ("median", "best", "selected_test")} for a in ABLATIONS}
    cmp["summary_minus_scores_only"] = {k: paired([r[k] for r in by["scores_only"]],
                                                  [r[k] for r in by["scores_summary"]]) for k in ("median", "best")}
    verdict = make_verdict(cmp, int(sum(beats)), len(seeds))
    print(table([[a, fmt(summ[a]["median"]), fmt(summ[a]["best"]), fmt(summ[a]["n_above_zero_shot"], 1),
                  fmt(summ[a]["selected_test"]), fmt(summ[a]["trace_files_per_iter"], 1)] for a in ARMS],
                ["arm", "median search", "best search", "#>zero-shot", "selected test", "trace files read/iter"]))
    print("verdict:", verdict)
    f, ax = plt().subplots(figsize=(6, 4))
    ax.boxplot([[c for r in by[a] for c in r["candidate_scores"]] for a in ARMS], tick_labels=ARMS)
    ax.axhline(np.mean([r["fewshot_all"] for r in rows]), ls="--", c="gray", label="fewshot_all (mean)")
    ax.axhline(np.mean([r["zero_shot"] for r in rows]), ls=":", c="gray", label="zero-shot (mean)")
    ax.set_ylabel("search accuracy of proposed candidates")
    ax.set_title(f"M1 history ablation ({args.llm}, {args.seeds} seeds)")
    ax.legend(fontsize=8)
    f.tight_layout()
    hint = f"_hint{args.budget_hint}" if args.budget_hint else ""
    png = figure_path("m1_history_ablation" + hint, args)
    f.savefig(png, dpi=120)
    save("m1_history_ablation" + ("_live" if args.live else "") + hint, {
        "claim": "Full-history (raw traces) access is the key ingredient; summaries do not recover it [MH Table 3]",
        "config": {"llm": args.llm, "seeds": args.seeds, "iterations": n_iterations(args),
                   "k": 2, "domain": "memoclassify", "proposer": "RewriteProposer" if args.live else "MockProposer",
                   "budget_hint": args.budget_hint or 11000, "trace_detail": "full"},
        "per_seed": rows, "summary": summ, "paired": cmp, "verdict": verdict, "figure": str(png), "caveat": CAVEAT},
        args.out)


if __name__ == "__main__":
    main()
