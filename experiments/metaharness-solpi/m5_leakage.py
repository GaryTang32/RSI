"""M5 - No explicit guard: overfitting through the history is possible; a leakage screen stops it.

    python experiments/metaharness-solpi/m5_leakage.py [--llm sim|claude:haiku] [--seeds N] [--quick] [--leak-rate P]
"""
from __future__ import annotations

import sys
from functools import partial
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import fmt, fresh_dir, live_llm, paired, parse_args, pool_map, save, summarize, table  # noqa: E402
from mh_common import drop_traces  # noqa: E402

import numpy as np  # noqa: E402

from rsi.domains.memoclassify import make_domain  # noqa: E402
from rsi.metaharness import Config, LeakageScreen, MemoClassifyLibrary, MockProposer, RewriteProposer, run  # noqa: E402

ARMS = {"screen_off": False, "screen_on": True}
KEYS = ("leaky_proposed", "leaky_evaluated", "leaky_rejected", "leaky_on_frontier", "gap", "gain_search", "gain_test")


def n_iterations(args) -> int:
    return 2 if args.live else (4 if args.quick else 8)


def job(args, spec) -> dict:
    arm, seed = spec
    dom = make_domain(seed=seed, leaky=True, scale=0.5 if args.live else 1.0)
    prop = RewriteProposer(live_llm(args.llm)) if args.live else \
        MockProposer(MemoClassifyLibrary(), leak_rate=args.leak_rate, seed=seed)
    res = run(dom, dom.seed_artifact("fewshot_all"), llm_task=dom.make_model("A"), proposer=prop,
              config=Config(iterations=n_iterations(args), k=2, seed=seed, leakage_screen=ARMS[arm]),
              out_dir=fresh_dir("m5", f"{arm}_s{seed}_{args.llm.replace(':', '_')}"), baselines=dom.baselines())
    st = res.loop.store
    metas = {nm: st.meta(nm) for nm in st.names()}
    screen = LeakageScreen.for_domain(dom)            # post-hoc audit of every candidate, in both arms
    base = dom.seed_artifact("fewshot_all")
    leaky = {nm for nm, m in metas.items() if m.get("kind") != "baseline" and screen.check(st.artifact(nm), base)}
    front = {p["system"] for p in res.meta["frontier"]["_pareto"]}
    best = res.meta["best_system"]
    test = res.meta["final"]["splits"]["test"]["results"]
    sel = st.scores(best)
    sel_test = test.get(best, {}).get("score")
    fa_s, fa_t = st.scores("fewshot_all")["score"], test["fewshot_all"]["score"]
    leaky_ds = [u for u in sel["per_unit"] if u.startswith("ds_leaky")]
    drop_traces(st.root)
    return {"arm": arm, "seed": seed, "leaky_proposed": len(leaky),
            "leaky_evaluated": sum(1 for nm in leaky if metas[nm].get("status") == "evaluated"),
            "leaky_rejected": sum(1 for nm in leaky if metas[nm].get("status") == "rejected_leakage"),
            "leaky_on_frontier": len(leaky & front), "selected_is_leaky": best in leaky,
            "selected": best, "selected_search": sel["score"], "selected_test": sel_test,
            "gap": sel["score"] - sel_test if sel_test is not None else None,
            "gain_search": sel["score"] - fa_s, "gain_test": (sel_test - fa_t) if sel_test is not None else None,
            "selected_leaky_ds_search": sel["per_unit"].get(leaky_ds[0]) if leaky_ds else None,
            "screen": res.meta["screen"]}


def main():
    args = parse_args(__doc__.splitlines()[0],
                      extra=lambda ap: ap.add_argument("--leak-rate", type=float, default=0.3))
    rows = pool_map(partial(job, args), [(a, s) for s in range(args.seeds) for a in ARMS], args.workers)
    by = {a: sorted([r for r in rows if r["arm"] == a], key=lambda r: r["seed"]) for a in ARMS}
    summ = {a: {k: summarize([r[k] for r in by[a]]) for k in KEYS} for a in ARMS}
    for a in ARMS:
        summ[a]["share_selected_leaky"] = float(np.mean([r["selected_is_leaky"] for r in by[a]]))
    gap_cmp = paired([r["gap"] for r in by["screen_on"]], [r["gap"] for r in by["screen_off"]])
    off, on = summ["screen_off"], summ["screen_on"]
    off_leaks = off["leaky_on_frontier"]["mean"] > 0
    on_blocks = on["leaky_evaluated"]["mean"] == 0 and on["leaky_rejected"]["mean"] > 0
    gap_grows = gap_cmp.get("mean_diff", 0) > 0
    verdict = (("REPRODUCED" if off_leaks and on_blocks and gap_grows else "PARTIAL" if on_blocks else
                "NOT REPRODUCED")
               + f": screen OFF -> {off['leaky_on_frontier']['mean']:.1f} leaky candidates on the frontier, "
               f"selected leaky in {100 * off['share_selected_leaky']:.0f}% of runs; screen ON -> "
               f"{on['leaky_rejected']['mean']:.1f} rejected before evaluation, 0 evaluated. "
               f"Practised-vs-unseen gap (off - on) = {gap_cmp.get('mean_diff', float('nan')):+.3f} "
               f"[{gap_cmp.get('lo', float('nan')):+.3f}, {gap_cmp.get('hi', float('nan')):+.3f}]")
    print(table([[a] + [fmt(summ[a][k], 2) for k in KEYS] for a in ARMS], ["arm"] + list(KEYS)))
    print("verdict:", verdict)
    save("m5_leakage" + ("_live" if args.live else ""), {
        "claim": "Without a guard, search-set leakage reaches the frontier and widens the practised-vs-unseen gap; "
                 "a pre-evaluation leakage screen rejects it [doc; MH pilot]",
        "config": {"llm": args.llm, "seeds": args.seeds, "leak_rate": args.leak_rate},
        "per_seed": rows, "summary": summ, "gap_off_minus_on": gap_cmp, "verdict": verdict,
        "caveat": "The leak rate is injected; the experiment measures what happens once a proposer leaks, not how "
                  "often a real LLM proposer leaks."}, args.out)


if __name__ == "__main__":
    main()
