"""E4b (retry round 2, preregistered P8 in docs/methods/rrsi/claims-audit.md section 6): claim L15.

"Noise chasing is controlled: candidates that win by luck are not kept." E4's null pool (every
proposed mechanism has exactly zero effect and zero cost), at 5x E4's seeds (worlds 0-99, T = 20).
Arms: default rule with the code-default bootstrap delta; R = 5 repeated base evaluations; the coding
within-band rule (w_s = 0); and a keep-if-better baseline (greedy selection, no floor, no band).

Metrics: the round-0 false-gain rate (a null's measured dS > delta against H_0's unselected
measurement; pooled count with a Wilson 95% interval) and the kept-null rate (null candidates accepted
into the incumbent / null candidates evaluated; per run, and pooled with a Wilson interval).
Pass: with the code-default delta, both upper bounds <= 5%.
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import paired, pmap, run_hw, save, strip_curves, summarize  # noqa: E402
from e4_noise_floor import NULL_SHARES, NULL_WORLD  # noqa: E402

from rsi.rrsi import RegularizerSwitches  # noqa: E402

ARMS = {"bootstrap delta (code default)": (RegularizerSwitches.full(), {}),
        "repeat R=5 delta": (RegularizerSwitches.full(), {"calibration_repeats": 5}),
        "coding within-band rule (w_s=0)": (RegularizerSwitches.full(), {"w_s": 0.0}),
        "keep-if-better (greedy)": (RegularizerSwitches.full().but(selection="greedy", name="greedy"), {})}


def wilson(k: int, n: int, z: float = 1.96) -> dict:
    if n == 0:
        return {"rate": None, "lo": None, "hi": None, "k": k, "n": n}
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return {"rate": p, "lo": max(0.0, c - h), "hi": min(1.0, c + h), "k": k, "n": n}


def main():
    ap = argparse.ArgumentParser(description="E4b: lucky nulls")
    ap.add_argument("--seeds", type=int, default=100)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--T", type=int, default=20)
    a = ap.parse_args()
    jobs = [{"seed": s, "arm": sw, "label": lab, "cfg": {"T": a.T, **cfg}, "world": NULL_WORLD,
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
    summ = summarize(rows, metrics=("kept_null_rate", "evolve_gain", "delta", "token_ratio"))
    vs_greedy = {lab: paired(rows, "keep-if-better (greedy)", lab, "kept_null_rate") for lab in ARMS
                 if lab != "keep-if-better (greedy)"}
    d = res["bootstrap delta (code default)"]
    checks = {"round-0 false-gain rate upper bound <= 5% (code-default delta)": d["round0_false_gain"]["hi"] <= 0.05,
              "kept-null rate upper bound <= 5% (code-default delta)": d["kept_null_pooled"]["hi"] <= 0.05,
              "keeps fewer nulls than keep-if-better (paired CI < 0)":
                  vs_greedy["bootstrap delta (code default)"]["hi"] < 0}
    verdict = ("REPRODUCED" if checks["round-0 false-gain rate upper bound <= 5% (code-default delta)"]
               and checks["kept-null rate upper bound <= 5% (code-default delta)"] else
               "PARTIAL" if checks["keeps fewer nulls than keep-if-better (paired CI < 0)"] else "NOT REPRODUCED")
    out = {"experiment": "E4b lucky nulls (retry round 2: P8)",
           "config": {"seeds": a.seeds, "T": a.T, "null_world": NULL_WORLD},
           "rates": res, "summary": summ, "kept_null_rate_minus_greedy": vs_greedy, "checks": checks,
           "verdict": verdict, "rows": strip_curves(rows)}
    save("e4b_null_luck", out)
    for lab, v in res.items():
        print(lab, {k: (round(x["rate"], 4), round(x["lo"], 4), round(x["hi"], 4), x["n"]) for k, x in v.items()})
    print(vs_greedy)
    print(checks, verdict)


if __name__ == "__main__":
    main()
