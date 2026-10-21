"""E4c (preregistered P8b, claim L15): E4b with nulls that cost tokens (U(0, 0.02)) but stay effect-free.

Run: python experiments/rrsi/e4c_costly_nulls.py [--seeds 100] [--workers 2] [--T 20]
"""
from _common import retry_args, save, strip_curves, summarize
from e4_noise_floor import NULL_WORLD
from e4b_null_luck import FEWER, minus_greedy, null_checks, print_rates, run_null_arms

COSTLY_NULL_WORLD = {**NULL_WORLD, "null_cost": (0.0, 0.02), "context_penalty": 0.0}


def main():
    a = retry_args("E4c: lucky nulls that cost tokens")
    rows, rates = run_null_arms(a, COSTLY_NULL_WORLD)
    summ = summarize(rows, metrics=("kept_null_rate", "evolve_gain", "ood_gain", "delta", "token_ratio"))
    vs_greedy = minus_greedy(rows, "kept_null_rate")
    tok_vs_greedy = minus_greedy(rows, "token_ratio")
    checks = null_checks(rates, vs_greedy)
    verdict = "PARTIAL" if checks[FEWER] else "NOT REPRODUCED"   # REPRODUCED would also need P8's (failed) bounds
    out = {"experiment": "E4c lucky nulls that cost tokens (retry round 2 review follow-up: P8b)",
           "config": {"seeds": a.seeds, "T": a.T, "null_world": COSTLY_NULL_WORLD},
           "rates": rates, "summary": summ, "kept_null_rate_minus_greedy": vs_greedy,
           "token_ratio_minus_greedy": tok_vs_greedy, "checks": checks,
           "verdict_L15_over_P8_and_P8b": verdict, "rows": strip_curves(rows)}
    save("e4c_costly_nulls", out)
    print_rates(rates)
    print(vs_greedy)
    print(tok_vs_greedy)
    print(checks, verdict)


if __name__ == "__main__":
    main()
