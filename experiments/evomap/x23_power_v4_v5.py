"""X23 (retry round 2, P11): the trials and cost a live test of the Strategy-Genes ablations V4 / V5 would need.
Run: python experiments/evomap/x23_power_v4_v5.py
"""
from __future__ import annotations

import json
import math

from _common import save

from rsi.core.llm import _PRICES_PER_MTOK

Z_A, Z_B = 1.959964, 0.841621      # two-sided alpha 0.05, power 0.8
CAP_USD = 4.0
TOKENS_PER_TRIAL = 1623            # the smallest kata trial of the live smoke (first cycle)
TESTS = {
    "V4 complementary (44.9) < conflicting (53.2)": (0.449, 0.532, 2),
    "V4 single (54.0) > conflicting (53.2)": (0.540, 0.532, 2),
    "V5 warnings only (54.4) > strategy only (52.3)": (0.544, 0.523, 2),
    "V5 warnings only (54.4) > failure first (50.5)": (0.544, 0.505, 2),
}


def n_per_arm(p1: float, p2: float) -> int:
    """Two-proportion z test: n = (z_a + z_b)^2 (p1 q1 + p2 q2) / d^2."""
    d = abs(p1 - p2)
    return math.ceil((Z_A + Z_B) ** 2 * (p1 * (1 - p1) + p2 * (1 - p2)) / d ** 2)


def main():
    pin, pout = next(v for k, v in _PRICES_PER_MTOK.items() if "haiku" in k)
    # cost is an estimate, not a bound: solve prompts are input-heavy, so both splits are reported
    usd_trial = (TOKENS_PER_TRIAL / 2 * pin + TOKENS_PER_TRIAL / 2 * pout) / 1e6          # even split
    usd_trial_90 = (TOKENS_PER_TRIAL * 0.9 * pin + TOKENS_PER_TRIAL * 0.1 * pout) / 1e6   # input-heavy split
    rows = {}
    for name, (p1, p2, arms) in TESTS.items():
        n = n_per_arm(p1, p2)
        rows[name] = {"p1": p1, "p2": p2, "n_per_arm": n, "trials": n * arms,
                      "usd_estimate_even_split": round(n * arms * usd_trial, 2),
                      "usd_estimate_90_10_split": round(n * arms * usd_trial_90, 2),
                      "fits_cap_even_split": n * arms * usd_trial <= CAP_USD,
                      "fits_cap_90_10_split": n * arms * usd_trial_90 <= CAP_USD}
    # the smallest set that tests V4 and V5 as stated: 3 arms each at the largest n of its tests
    n4 = max(rows[k]["n_per_arm"] for k in rows if k.startswith("V4"))
    n5 = max(rows[k]["n_per_arm"] for k in rows if k.startswith("V5"))
    total = {"V4_trials": 3 * n4, "V5_trials": 3 * n5,
             "usd_estimate_even_split": round((3 * n4 + 3 * n5) * usd_trial, 2),
             "usd_estimate_90_10_split": round((3 * n4 + 3 * n5) * usd_trial_90, 2)}
    paper = {"trials": 4590, "note": "if spread over >= 17 conditions (progressive 4, perturbation 4, failure 4, "
             "composition 4, main 3 minus overlaps), about 270 trials per condition: SE of a difference ~ 4.3 pp"}
    paper["se_diff_pp_at_270"] = round(100 * math.sqrt(2 * 0.5 * 0.5 / 270), 1)
    out = {"config": {"alpha": 0.05, "power": 0.8, "usd_per_trial_estimate_even_split": round(usd_trial, 5),
                      "usd_per_trial_estimate_90_10_split": round(usd_trial_90, 5),
                      "tokens_per_trial": TOKENS_PER_TRIAL, "haiku_prices_per_mtok": [pin, pout], "cap_usd": CAP_USD,
                      "preregistration": "docs/methods/evomap/claims-audit.md#retry-round-2-preregistration"},
           "tests": rows, "total": total, "paper_resolution": paper,
           "verdict": {"primary_reason_not_testable": "no task set with headroom (Haiku 1.0 on katas without genes; "
                                                      "the paper's 45 scenarios are unreleased)",
                       "any_test_fits_cap_90_10_split": any(r["fits_cap_90_10_split"] for r in rows.values()),
                       "any_test_fits_cap_even_split": any(r["fits_cap_even_split"] for r in rows.values()),
                       "full_v4_v5_fits_cap": total["usd_estimate_90_10_split"] <= CAP_USD}}
    save("x23_power_v4_v5", out)
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
