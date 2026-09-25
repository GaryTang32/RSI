"""M1 - Meta-Harness proposer-interface ablation: scores only vs scores + summaries vs full traces.

Claim [paper:MH Table 3]: "Access to raw execution traces is the key ingredient"; median/best search
accuracy of proposed candidates is far higher with full history (50.0/56.7) than with scores + code
(34.6/41.3) or scores + code + LLM summaries (34.9/38.7); summaries "do not recover the missing signal".

Setup: MemoClassify (3 search datasets, frozen MemoLM-A), baselines no_memory + fewshot_all, N iterations
x k = 2 candidates, the SAME proposer in three history modes. Offline the proposer is the deterministic
MockProposer (its choice rule may only use what the view exposes, so this checks the machinery and the
information channel, not LLM behaviour); ``--llm claude:haiku`` uses a RewriteProposer on haiku.

    python experiments/metaharness-solpi/m1_history_ablation.py [--llm sim|claude:haiku] [--seeds N] [--quick]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import RUNS, figure_path, fmt, fresh_dir, live_llm, paired, parse_args, plt, pool_map, save, summarize, table  # noqa: E402

import numpy as np  # noqa: E402

from rsi.domains.memoclassify import make_domain  # noqa: E402
from rsi.metaharness import Config, run  # noqa: E402

ARMS = ["scores_only", "scores_summary", "full"]
ARGS = None


def job(spec):
    arm, seed = spec
    dom = make_domain(seed=seed, scale=0.5 if ARGS.live else 1.0)
    llm_p = live_llm(ARGS.llm) if ARGS.live else None
    n_iter = 2 if ARGS.live else (4 if ARGS.quick else 8)
    res = run(dom, dom.seed_artifact("fewshot_all"), llm_task=dom.make_model("A"), llm_propose=llm_p,
              config=Config(iterations=n_iter, k=2, history_mode=arm, seed=seed, validate_in_subprocess=True),
              out_dir=fresh_dir("m1", f"{arm}_s{seed}_{ARGS.llm.replace(':', '_')}"), baselines=dom.baselines())
    loop = res.loop
    names = loop.store.names()
    base = {n: loop.store.scores(n)["score"] for n in dom.baselines()}
    cands = [loop.store.scores(n)["score"] for n in names if n not in base and loop.store.scores(n)]
    sessions = [r for r in res.trajectory]
    kinds = [loop.store.sessions_dir() / f"iter{r['iteration']:03d}" / "meta.json" for r in sessions]
    import json
    km = [json.loads(p.read_text())["files_read_by_kind"] for p in kinds if p.exists()]
    best_name = res.meta["best_system"]
    test = res.meta["final"]["splits"]["test"]["results"]
    return {"arm": arm, "seed": seed, "n_candidates": len(cands), "median": float(np.median(cands)) if cands else None,
            "best": max(cands) if cands else None, "zero_shot": base["no_memory"], "fewshot_all": base["fewshot_all"],
            "n_above_zero_shot": sum(c > base["no_memory"] for c in cands),
            "n_above_fewshot_all": sum(c > base["fewshot_all"] for c in cands),
            "selected": best_name, "selected_search": res.meta["frontier"]["_best"]["score"],
            "selected_test": test.get(best_name, {}).get("score"), "fewshot_all_test": test["fewshot_all"]["score"],
            "files_read_per_iter": float(np.mean([sum(k.values()) for k in km])) if km else 0.0,
            "trace_files_per_iter": float(np.mean([k["traces"] for k in km])) if km else 0.0,
            "view_chars_per_iter": float(np.mean([r["view_chars"] for r in sessions])),
            "read_chars_per_iter": float(np.mean([r["read_chars"] for r in sessions])),
            "proposer_tokens": float(sum(r["proposer_tokens"] for r in sessions)),
            "proposer_usd": float(sum(r["proposer_usd"] for r in sessions)),
            "candidate_scores": cands}


def main():
    global ARGS
    ARGS = parse_args(__doc__.splitlines()[0])
    jobs = [(a, s) for s in range(ARGS.seeds) for a in ARMS]
    rows = pool_map(job, jobs, ARGS.workers)
    by = {a: [r for r in rows if r["arm"] == a] for a in ARMS}
    summ = {a: {k: summarize([r[k] for r in by[a]]) for k in
                ("median", "best", "n_above_zero_shot", "n_above_fewshot_all", "selected_search", "selected_test",
                 "files_read_per_iter", "trace_files_per_iter", "read_chars_per_iter", "proposer_tokens")}
            for a in ARMS}
    cmp = {f"full_minus_{a}": {k: paired([r[k] for r in by[a]], [r[k] for r in by["full"]])
                               for k in ("median", "best", "selected_test")} for a in ("scores_only", "scores_summary")}
    cmp["summary_minus_scores_only"] = {k: paired([r[k] for r in by["scores_only"]],
                                                  [r[k] for r in by["scores_summary"]]) for k in ("median", "best")}
    full_wins = all(cmp[f"full_minus_{a}"]["median"].get("mean_diff", 0) > 0 and
                    cmp[f"full_minus_{a}"]["best"].get("mean_diff", 0) >= 0 for a in ("scores_only", "scores_summary"))
    ci_excl = all(cmp[f"full_minus_{a}"]["median"].get("lo", -1) > 0 for a in ("scores_only", "scores_summary"))
    summ_no_better = cmp["summary_minus_scores_only"]["median"].get("lo", 0) <= 0
    best_sig = all(cmp[f"full_minus_{a}"]["best"].get("lo", -1) > 0 for a in ("scores_only", "scores_summary"))
    # the spec's confirming outcome has two parts: full > both ablations (median and best) AND summaries no
    # better than scores-only; REPRODUCED needs both, a failed sub-claim makes the verdict PARTIAL
    verdict = ("REPRODUCED" if full_wins and ci_excl and summ_no_better else "PARTIAL" if full_wins
               else "NOT REPRODUCED") + \
        (": full history beats both ablations on median (CI excludes 0) and on best (mean)" if full_wins and ci_excl
         else ": full history is ahead on average but not significantly on every comparison" if full_wins else
         ": full history does not beat the ablations") + \
        ("" if best_sig else " (best-candidate CI touches or includes 0 in at least one comparison)") + \
        ("; summaries no better than scores-only (CI includes 0 or below), as in the paper" if summ_no_better else
         "; sub-claim NOT reproduced: summaries DO help over scores-only here (paper: they do not)")
    print(table([[a, fmt(summ[a]["median"]), fmt(summ[a]["best"]), fmt(summ[a]["n_above_zero_shot"], 1),
                  fmt(summ[a]["selected_test"]), fmt(summ[a]["trace_files_per_iter"], 1)] for a in ARMS],
                ["arm", "median search", "best search", "#>zero-shot", "selected test", "trace files read/iter"]))
    print("verdict:", verdict)
    fig = plt()
    f, ax = fig.subplots(figsize=(6, 4))
    data = [[c for r in by[a] for c in r["candidate_scores"]] for a in ARMS]
    ax.boxplot(data, tick_labels=ARMS)
    ax.axhline(np.mean([r["fewshot_all"] for r in rows]), ls="--", c="gray", label="fewshot_all (mean)")
    ax.axhline(np.mean([r["zero_shot"] for r in rows]), ls=":", c="gray", label="zero-shot (mean)")
    ax.set_ylabel("search accuracy of proposed candidates")
    ax.set_title(f"M1 history ablation ({ARGS.llm}, {ARGS.seeds} seeds)")
    ax.legend(fontsize=8)
    f.tight_layout()
    out_png = figure_path("m1_history_ablation", ARGS)
    f.savefig(out_png, dpi=120)
    save("m1_history_ablation" + ("" if not ARGS.live else "_live"), {
        "claim": "Full-history (raw traces) access is the key ingredient; summaries do not recover it [MH Table 3]",
        "config": {"llm": ARGS.llm, "seeds": ARGS.seeds, "iterations": 2 if ARGS.live else (4 if ARGS.quick else 8),
                   "k": 2, "domain": "memoclassify", "proposer": "RewriteProposer" if ARGS.live else "MockProposer"},
        "per_seed": rows, "summary": summ, "paired": cmp, "verdict": verdict, "figure": str(out_png),
        "caveat": "Offline, the proposer is a deterministic MockProposer whose diagnosis rules are written by us; "
                  "the result shows the history views gate the information channel as intended, not that an LLM "
                  "proposer benefits the same way."}, ARGS.out)


if __name__ == "__main__":
    main()
