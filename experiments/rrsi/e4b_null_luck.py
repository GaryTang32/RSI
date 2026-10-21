"""E4b (preregistered P8, claim L15): lucky null candidates are not kept; E4's null pool at 5x the seeds.

Run: python experiments/rrsi/e4b_null_luck.py [--seeds 100] [--workers 2] [--T 20]
"""
import math

from _common import paired, pmap, retry_args, run_hw, save, strip_curves, summarize
from e4_noise_floor import NULL_SHARES, NULL_WORLD

from rsi.rrsi import RegularizerSwitches

ARMS = {"bootstrap delta (code default)": (RegularizerSwitches.full(), {}),
        "repeat R=5 delta": (RegularizerSwitches.full(), {"calibration_repeats": 5}),
        "coding within-band rule (w_s=0)": (RegularizerSwitches.full(), {"w_s": 0.0}),
        "keep-if-better (greedy)": (RegularizerSwitches.full().but(selection="greedy", name="greedy"), {})}
GREEDY = "keep-if-better (greedy)"
FEWER = "keeps fewer nulls than keep-if-better (paired CI < 0)"


def wilson(k: int, n: int, z: float = 1.96) -> dict:
    if n == 0:
        return {"rate": None, "lo": None, "hi": None, "k": k, "n": n}
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return {"rate": p, "lo": max(0.0, c - h), "hi": min(1.0, c + h), "k": k, "n": n}


def run_null_arms(a, world: dict) -> tuple[list, dict]:
    """Rows of every arm on a null-only world, and per arm the pooled Wilson rates."""
    jobs = [{"seed": s, "arm": sw, "label": lab, "cfg": {"T": a.T, **cfg}, "world": world,
             "proposer": {"shares": NULL_SHARES, "prune_p": 0.0}} for lab, (sw, cfg) in ARMS.items()
            for s in range(a.seeds)]
    rows = pmap(run_hw, jobs, a.workers)
    for r in rows:
        r["kept_null_rate"] = r["n_accepted"] / r["null_candidates"] if r["null_candidates"] else None

    def pooled(lab: str, k: str, n: str) -> dict:
        return wilson(sum(r[k] for r in rows if r["label"] == lab), sum(r[n] for r in rows if r["label"] == lab))
    rates = {lab: {"round0_false_gain": pooled(lab, "null_false_gain_r0", "null_candidates_r0"),
                   "false_gain_whole_run": pooled(lab, "null_false_gain", "null_candidates"),
                   "kept_null_pooled": pooled(lab, "n_accepted", "null_candidates")} for lab in ARMS}
    return rows, rates


def minus_greedy(rows: list, metric: str) -> dict:
    return {lab: paired(rows, GREEDY, lab, metric) for lab in ARMS if lab != GREEDY}


def null_checks(rates: dict, vs_greedy: dict) -> dict:
    d = rates["bootstrap delta (code default)"]
    return {"round-0 false-gain rate upper bound <= 5% (code-default delta)": d["round0_false_gain"]["hi"] <= 0.05,
            "kept-null rate upper bound <= 5% (code-default delta)": d["kept_null_pooled"]["hi"] <= 0.05,
            FEWER: vs_greedy["bootstrap delta (code default)"]["hi"] < 0}


def print_rates(rates: dict) -> None:
    for lab, v in rates.items():
        print(lab, {k: (round(x["rate"], 4), round(x["lo"], 4), round(x["hi"], 4), x["n"]) for k, x in v.items()})


def main():
    a = retry_args("E4b: lucky nulls")
    rows, rates = run_null_arms(a, NULL_WORLD)
    summ = summarize(rows, metrics=("kept_null_rate", "evolve_gain", "delta", "token_ratio"))
    vs_greedy = minus_greedy(rows, "kept_null_rate")
    checks = null_checks(rates, vs_greedy)
    bounds = [v for k, v in checks.items() if k != FEWER]
    verdict = "REPRODUCED" if all(bounds) else "PARTIAL" if checks[FEWER] else "NOT REPRODUCED"
    out = {"experiment": "E4b lucky nulls (retry round 2: P8)",
           "config": {"seeds": a.seeds, "T": a.T, "null_world": NULL_WORLD},
           "rates": rates, "summary": summ, "kept_null_rate_minus_greedy": vs_greedy, "checks": checks,
           "verdict": verdict, "rows": strip_curves(rows)}
    save("e4b_null_luck", out)
    print_rates(rates)
    print(vs_greedy)
    print(checks, verdict)


if __name__ == "__main__":
    main()
