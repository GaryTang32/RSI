"""M6 - Winner's curse in incumbent selection, and the optional noise-aware re-evaluation.

    python experiments/metaharness-solpi/m6_noise_reeval.py [--seeds N] [--quick] [--workers W]
"""
from __future__ import annotations

import json
import sys
from functools import partial
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import fmt, fresh_dir, paired, parse_args, pool_map, save, summarize, table  # noqa: E402
from mh_common import drop_traces  # noqa: E402

import numpy as np  # noqa: E402

from rsi.core import Evaluator  # noqa: E402
from rsi.domains.memoclassify import make_domain  # noqa: E402
from rsi.metaharness import Config, run  # noqa: E402

ARMS = {"reeval_off": 0, "reeval_on": 4}
POST_SEEDS = list(range(100, 108))          # post-hoc seeds, disjoint from the loop's seeds 0..4
KEYS = ("selected_true_search", "selected_test_8seeds", "n_incumbent_changes", "n_false_changes",
        "winner_curse_selected", "n_reevaluations")


def incumbent_changes(trace_path: Path) -> tuple[str | None, list[tuple]]:
    """The first incumbent and every (before, after) incumbent change, from the run's trace."""
    ev = [json.loads(line) for line in trace_path.read_text().splitlines()]
    first = next((e["data"]["frontier"]["best"]["system"] for e in ev if e["kind"] == "state" and e["round"] == 0
                  and (e["data"].get("frontier") or {}).get("best")), None)
    changes = [(e["data"]["incumbent_before"], e["data"]["incumbent_after"]) for e in ev
               if e["kind"] == "decision" and e["data"]["incumbent_before"] != e["data"]["incumbent_after"]]
    return first, changes


def job(args, spec) -> dict:
    """A change is false when the new incumbent's 8-fresh-seed search mean is below the displaced one's."""
    arm, seed = spec
    dom = make_domain(seed=seed)
    out = fresh_dir("m6", f"{arm}_s{seed}")
    res = run(dom, dom.seed_artifact("fewshot_all"), llm_task=dom.make_model("A"),
              config=Config(iterations=4 if args.quick else 8, k=2, seed=seed, reeval_incumbent=ARMS[arm],
                            shadow_monitor=False),
              out_dir=out, baselines=dom.baselines())
    st = res.loop.store
    first, changes = incumbent_changes(out / "trace.jsonl")
    incumbents = list(dict.fromkeys([first] + [b for c in changes for b in c if b]))
    post = Evaluator(dom, dom.make_model("A"), workers=1)
    true = {nm: post.evaluate(st.artifact(nm), "evolve", seeds=POST_SEEDS).score for nm in incumbents if nm}
    false_changes = [c for c in changes if c[0] in true and c[1] in true and true[c[1]] < true[c[0]]]
    best = res.meta["best_system"]
    sealed = Evaluator(dom, dom.make_model("A"), workers=1, allow_sealed=True)
    sel_test8 = sealed.evaluate(st.artifact(best), "test", seeds=POST_SEEDS).score
    logged = {nm: (st.scores(nm) or {}).get("single_seed_score", (st.scores(nm) or {}).get("score")) for nm in true}
    drop_traces(st.root)
    sel = st.scores(best)
    return {"arm": arm, "seed": seed, "selected": best, "selected_logged_search": sel["score"],
            "selected_true_search": true.get(best), "selected_test_8seeds": sel_test8,
            "selected_test_1seed": res.meta["final"]["splits"]["test"]["results"].get(best, {}).get("score"),
            "n_incumbent_changes": len(changes), "n_false_changes": len(false_changes),
            "changes": changes, "false_changes": false_changes, "incumbent_true_search": true,
            "incumbent_logged_single_seed": logged, "n_reevaluations": res.meta.get("n_reevaluations", 0),
            "n_evaluated": res.meta["n_evaluated"],
            "winner_curse_selected": sel.get("single_seed_score", sel["score"]) - true.get(best, np.nan)}


def make_verdict(summ: dict, cmp: dict) -> str:
    off, on, test = summ["reeval_off"], summ["reeval_on"], cmp["selected_test_8seeds"]
    curse = off["winner_curse_selected"]["mean"] > 0
    fewer_false = cmp["n_false_changes"].get("mean_diff", 0) < 0
    better = test.get("lo", -1) > 0
    return ("winner's curse " + ("REPRODUCED" if curse else "not observed")
            + f": the faithful loop's selected harness scores {fmt(off['winner_curse_selected'])} "
            f"higher on its logged single seed than on 8 fresh seeds; "
            f"{100 * off['share_false_changes']:.0f}% of its incumbent changes are false "
            f"(8-seed mean below the displaced incumbent). Re-evaluation ON: false changes "
            f"{on['share_false_changes'] * 100:.0f}% "
            f"(paired diff in count {cmp['n_false_changes'].get('mean_diff', float('nan')):+.2f}), selected test "
            f"(8 seeds) on - off = {test.get('mean_diff', float('nan')):+.3f} "
            f"[{test.get('lo', float('nan')):+.3f}, {test.get('hi', float('nan')):+.3f}]"
            + (" (significant)" if better else " (not significant)")
            + f", at {on['n_reevaluations']['mean']:.1f} extra 5-seed evaluations per run"
            + ("" if fewer_false else "; it does NOT reduce false changes here"))


def main():
    args = parse_args(__doc__.splitlines()[0])
    rows = pool_map(partial(job, args), [(a, s) for s in range(args.seeds) for a in ARMS], args.workers)
    by = {a: sorted([r for r in rows if r["arm"] == a], key=lambda r: r["seed"]) for a in ARMS}
    summ = {a: {k: summarize([r[k] for r in by[a]]) for k in KEYS} for a in ARMS}
    for a in ARMS:
        tot = sum(r["n_incumbent_changes"] for r in by[a])
        summ[a]["share_false_changes"] = sum(r["n_false_changes"] for r in by[a]) / tot if tot else float("nan")
    cmp = {k: paired([r[k] for r in by["reeval_off"]], [r[k] for r in by["reeval_on"]])
           for k in ("selected_true_search", "selected_test_8seeds", "n_false_changes", "winner_curse_selected")}
    verdict = make_verdict(summ, cmp)
    print(table([[a] + [fmt(summ[a][k], 3) for k in KEYS] for a in ARMS], ["arm"] + list(KEYS)))
    print("verdict:", verdict)
    save("m6_noise_reeval", {
        "claim": "Single-seed selection on the search set suffers a winner's curse (spec A8.4, audit N5); the "
                 "optional Config.reeval_incumbent re-scores every new incumbent on more seeds",
        "config": {"seeds": args.seeds, "iterations": 4 if args.quick else 8, "k": 2, "reeval_incumbent": ARMS,
                   "post_hoc_seeds": POST_SEEDS, "domain": "memoclassify", "proposer": "MockProposer"},
        "per_seed": rows, "summary": summ, "paired_on_minus_off": cmp, "verdict": verdict,
        "caveat": "Re-evaluation is NOT part of the paper or the release (it is off by default). Re-evaluations "
                  "are not counted in eval_budget. The mock proposer and MemoLM are simulations; MemoLM's per-seed "
                  "noise is its eps / overflow noise."}, args.out)


if __name__ == "__main__":
    main()
