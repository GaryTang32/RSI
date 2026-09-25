"""M5 - No explicit guard: overfitting through the history is possible; a leakage screen stops it.

Claims [doc; paper:MH §4.3, A8.2-4; ye-blog]: Meta-Harness has "no explicit check for test-specific
edits"; a proposer that reads search-set traces can hard-code them, the frontier then rewards it, and the
practised-vs-unseen gap grows (RRSI: Meta-Harness practised best, +<1 point unseen). The experimental
pilot's pre-evaluation forbidden-reference check (``LeakageScreen``) rejects such candidates.

Setup: MemoClassify with an extra search dataset whose inputs carry memorisable reference ids; a
MockProposer that, with probability ``leak_rate`` per iteration, hard-codes (id/snippet -> label) pairs read
from the search traces. Arms: screen OFF (faithful Meta-Harness) vs ON (pilot). Metrics: leaky candidates
proposed / evaluated / on the frontier / selected, and the selected harness' search-minus-test gap.

    python experiments/metaharness-solpi/m5_leakage.py [--llm sim|claude:haiku] [--seeds N] [--quick]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import fmt, fresh_dir, live_llm, paired, parse_args, pool_map, save, summarize, table  # noqa: E402

import numpy as np  # noqa: E402

from rsi.domains.memoclassify import make_domain  # noqa: E402
from rsi.metaharness import Config, LeakageScreen, MemoClassifyLibrary, MockProposer, RewriteProposer, run  # noqa: E402

ARGS = None
ARMS = {"screen_off": False, "screen_on": True}


def job(spec):
    arm, seed = spec
    dom = make_domain(seed=seed, leaky=True, scale=0.5 if ARGS.live else 1.0)
    n = 2 if ARGS.live else (4 if ARGS.quick else 8)
    prop = RewriteProposer(live_llm(ARGS.llm)) if ARGS.live else \
        MockProposer(MemoClassifyLibrary(), leak_rate=ARGS.leak_rate, seed=seed)
    res = run(dom, dom.seed_artifact("fewshot_all"), llm_task=dom.make_model("A"), proposer=prop,
              config=Config(iterations=n, k=2, seed=seed, leakage_screen=ARMS[arm]),
              out_dir=fresh_dir("m5", f"{arm}_s{seed}_{ARGS.llm.replace(':', '_')}"), baselines=dom.baselines())
    st = res.loop.store
    metas = {nm: st.meta(nm) for nm in st.names()}
    screen = LeakageScreen.for_domain(dom)            # post-hoc audit of EVERY candidate (both arms)
    base = dom.seed_artifact("fewshot_all")
    leaky = {nm for nm, m in metas.items() if m.get("kind") != "baseline" and screen.check(st.artifact(nm), base)}
    fr = res.meta["frontier"]
    front = {p["system"] for p in fr["_pareto"]}
    best = res.meta["best_system"]
    test = res.meta["final"]["splits"]["test"]["results"]
    sel_search = st.scores(best)["score"]
    sel_test = test.get(best, {}).get("score")
    fa_s, fa_t = st.scores("fewshot_all")["score"], test["fewshot_all"]["score"]
    leaky_ds = [u for u in st.scores(best)["per_unit"] if u.startswith("ds_leaky")]
    return {"arm": arm, "seed": seed, "leaky_proposed": len(leaky),
            "leaky_evaluated": sum(1 for nm in leaky if metas[nm].get("status") == "evaluated"),
            "leaky_rejected": sum(1 for nm in leaky if metas[nm].get("status") == "rejected_leakage"),
            "leaky_on_frontier": len(leaky & front), "selected_is_leaky": best in leaky,
            "selected": best, "selected_search": sel_search, "selected_test": sel_test,
            "gap": sel_search - sel_test if sel_test is not None else None,
            "gain_search": sel_search - fa_s, "gain_test": (sel_test - fa_t) if sel_test is not None else None,
            "selected_leaky_ds_search": st.scores(best)["per_unit"].get(leaky_ds[0]) if leaky_ds else None,
            "screen": res.meta["screen"]}


def main():
    global ARGS
    ARGS = parse_args(__doc__.splitlines()[0], extra=lambda ap: ap.add_argument("--leak-rate", type=float,
                                                                                 default=0.3))
    rows = pool_map(job, [(a, s) for s in range(ARGS.seeds) for a in ARMS], ARGS.workers)
    by = {a: sorted([r for r in rows if r["arm"] == a], key=lambda r: r["seed"]) for a in ARMS}
    keys = ("leaky_proposed", "leaky_evaluated", "leaky_rejected", "leaky_on_frontier", "gap", "gain_search",
            "gain_test")
    summ = {a: {k: summarize([r[k] for r in by[a]]) for k in keys} for a in ARMS}
    for a in ARMS:
        summ[a]["share_selected_leaky"] = float(np.mean([r["selected_is_leaky"] for r in by[a]]))
    gap_cmp = paired([r["gap"] for r in by["screen_on"]], [r["gap"] for r in by["screen_off"]])
    off_leaks = summ["screen_off"]["leaky_on_frontier"]["mean"] > 0
    on_blocks = summ["screen_on"]["leaky_evaluated"]["mean"] == 0 and summ["screen_on"]["leaky_rejected"]["mean"] > 0
    gap_grows = gap_cmp.get("mean_diff", 0) > 0
    verdict = ("REPRODUCED" if off_leaks and on_blocks and gap_grows else "PARTIAL" if on_blocks else
               "NOT REPRODUCED") + \
        f": screen OFF -> {summ['screen_off']['leaky_on_frontier']['mean']:.1f} leaky candidates on the frontier, " \
        f"selected leaky in {100 * summ['screen_off']['share_selected_leaky']:.0f}% of runs; screen ON -> " \
        f"{summ['screen_on']['leaky_rejected']['mean']:.1f} rejected before evaluation, 0 evaluated. " \
        f"Practised-vs-unseen gap (off - on) = {gap_cmp.get('mean_diff', float('nan')):+.3f} " \
        f"[{gap_cmp.get('lo', float('nan')):+.3f}, {gap_cmp.get('hi', float('nan')):+.3f}]"
    print(table([[a] + [fmt(summ[a][k], 2) for k in keys] for a in ARMS], ["arm"] + list(keys)))
    print("verdict:", verdict)
    save("m5_leakage" + ("_live" if ARGS.live else ""), {
        "claim": "Without a guard, search-set leakage reaches the frontier and widens the practised-vs-unseen gap; "
                 "a pre-evaluation leakage screen rejects it [doc; MH pilot]",
        "config": {"llm": ARGS.llm, "seeds": ARGS.seeds, "leak_rate": ARGS.leak_rate},
        "per_seed": rows, "summary": summ, "gap_off_minus_on": gap_cmp, "verdict": verdict,
        "caveat": "The leak rate is injected; the experiment measures what happens once a proposer leaks, not how "
                  "often a real LLM proposer leaks."}, ARGS.out)


if __name__ == "__main__":
    main()
