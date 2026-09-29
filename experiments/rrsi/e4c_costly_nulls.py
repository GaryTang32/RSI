"""E4c (retry round 2 review follow-up, preregistered P8b in docs/methods/rrsi/claims-audit.md section 6): claim L15.

Exactly E4b (P8) with one change to the null pool: every null mechanism now costs tokens as in the
HarnessWorld default (null_cost = U(0, 0.02)), and context_penalty = 0 so that the cost does not turn
into a score effect (the nulls stay effect-free). In E4b the nulls cost nothing, which makes RRSI's
within-band shaped rule close to keep-if-better by construction. Same arms, metrics and thresholds.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import paired, pmap, run_hw, save, strip_curves, summarize  # noqa: E402
from e4_noise_floor import NULL_SHARES, NULL_WORLD  # noqa: E402
from e4b_null_luck import ARMS, wilson  # noqa: E402

COSTLY_NULL_WORLD = {**NULL_WORLD, "null_cost": (0.0, 0.02), "context_penalty": 0.0}


def main():
    ap = argparse.ArgumentParser(description="E4c: lucky nulls that cost tokens")
    ap.add_argument("--seeds", type=int, default=100)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--T", type=int, default=20)
    a = ap.parse_args()
    jobs = [{"seed": s, "arm": sw, "label": lab, "cfg": {"T": a.T, **cfg}, "world": COSTLY_NULL_WORLD,
             "proposer": {"shares": NULL_SHARES, "prune_p": 0.0}} for lab, (sw, cfg) in ARMS.items()
            for s in range(a.seeds)]
    rows = pmap(run_hw, jobs, a.workers)
    for r in rows:
        r["kept_null_rate"] = r["n_accepted"] / r["null_candidates"] if r["null_candidates"] else None
    res = {}
    for lab in ARMS:
        rs = [r for r in rows if r["label"] == lab]
        res[lab] = {"round0_false_gain": wilson(sum(r["null_false_gain_r0"] for r in rs),
                                                sum(r["null_candidates_r0"] for r in rs)),
                    "false_gain_whole_run": wilson(sum(r["null_false_gain"] for r in rs),
                                                   sum(r["null_candidates"] for r in rs)),
                    "kept_null_pooled": wilson(sum(r["n_accepted"] for r in rs), sum(r["null_candidates"] for r in rs))}
    summ = summarize(rows, metrics=("kept_null_rate", "evolve_gain", "ood_gain", "delta", "token_ratio"))
    vs_greedy = {lab: paired(rows, "keep-if-better (greedy)", lab, "kept_null_rate") for lab in ARMS
                 if lab != "keep-if-better (greedy)"}
    tok_vs_greedy = {lab: paired(rows, "keep-if-better (greedy)", lab, "token_ratio") for lab in ARMS
                     if lab != "keep-if-better (greedy)"}
    d = res["bootstrap delta (code default)"]
    checks = {"round-0 false-gain rate upper bound <= 5% (code-default delta)": d["round0_false_gain"]["hi"] <= 0.05,
              "kept-null rate upper bound <= 5% (code-default delta)": d["kept_null_pooled"]["hi"] <= 0.05,
              "keeps fewer nulls than keep-if-better (paired CI < 0)":
                  vs_greedy["bootstrap delta (code default)"]["hi"] < 0}
    # P8b mapping: REPRODUCED would also need P8's bounds, which failed -> at most PARTIAL.
    verdict = ("PARTIAL" if checks["keeps fewer nulls than keep-if-better (paired CI < 0)"] else "NOT REPRODUCED")
    out = {"experiment": "E4c lucky nulls that cost tokens (retry round 2 review follow-up: P8b)",
           "config": {"seeds": a.seeds, "T": a.T, "null_world": COSTLY_NULL_WORLD},
           "rates": res, "summary": summ, "kept_null_rate_minus_greedy": vs_greedy,
           "token_ratio_minus_greedy": tok_vs_greedy, "checks": checks,
           "verdict_L15_over_P8_and_P8b": verdict, "rows": strip_curves(rows)}
    save("e4c_costly_nulls", out)
    for lab, v in res.items():
        print(lab, {k: (round(x["rate"], 4), round(x["lo"], 4), round(x["hi"], 4), x["n"]) for k, x in v.items()})
    print(vs_greedy)
    print(tok_vs_greedy)
    print(checks, verdict)


if __name__ == "__main__":
    main()
