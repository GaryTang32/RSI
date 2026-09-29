"""X23 (retry round 2, P11): could a live run on this box test the Strategy-Genes ablations (V4, V5)?

V4: a single gene 54.0 %, two conflicting genes 53.2 %, two complementary genes 44.9 % (the claim: complementary <
conflicting, and single is best). V5: failure warnings only 54.4 % vs strategy only 52.3 % vs failure-first 50.5 %
(the claim: failures work best as compact AVOID warnings). Offline X1-X3 cannot test these: the kata simulator's
effects are knobs we set. A live test needs a model with headroom on the tasks (Haiku is at 1.0 on our katas without
genes) and enough trials per arm.

Two-proportion z test, two-sided alpha = 0.05, power 0.8: n per arm = (z_a + z_b)^2 (p1 q1 + p2 q2) / d^2.
Cost per trial: the smallest kata trial in our live smoke used 1,623 tokens (first cycle); at Haiku list prices
(rsi.core.llm price table) and an even input / output split that is the LOWER bound below; the CLI wrapper's own
system prompt only adds to it.

Run: python experiments/evomap/x23_power_v4_v5.py
"""
from __future__ import annotations

import math

from _common import save

from rsi.core.llm import _PRICES_PER_MTOK

Z_A, Z_B = 1.959964, 0.841621
CAP_USD = 4.0
TOKENS_PER_TRIAL = 1623


def n_per_arm(p1: float, p2: float) -> int:
    d = abs(p1 - p2)
    return math.ceil((Z_A + Z_B) ** 2 * (p1 * (1 - p1) + p2 * (1 - p2)) / d ** 2)


def main():
    pin, pout = next(v for k, v in _PRICES_PER_MTOK.items() if "haiku" in k)
    usd_trial = (TOKENS_PER_TRIAL / 2 * pin + TOKENS_PER_TRIAL / 2 * pout) / 1e6
    tests = {
        "V4 complementary (44.9) < conflicting (53.2)": (0.449, 0.532, 2),
        "V4 single (54.0) > conflicting (53.2)": (0.540, 0.532, 2),
        "V5 warnings only (54.4) > strategy only (52.3)": (0.544, 0.523, 2),
        "V5 warnings only (54.4) > failure first (50.5)": (0.544, 0.505, 2),
    }
    rows = {}
    for name, (p1, p2, arms) in tests.items():
        n = n_per_arm(p1, p2)
        rows[name] = {"p1": p1, "p2": p2, "n_per_arm": n, "trials": n * arms,
                      "usd_lower_bound": round(n * arms * usd_trial, 2),
                      "fits_cap": n * arms * usd_trial <= CAP_USD}
    # the smallest set that tests V4 and V5 as stated: 4 V4 arms + 3 V5 arms at the largest n each needs
    n4 = max(rows[k]["n_per_arm"] for k in rows if k.startswith("V4"))
    n5 = max(rows[k]["n_per_arm"] for k in rows if k.startswith("V5"))
    total = {"V4_trials": 3 * n4, "V5_trials": 3 * n5,
             "usd_lower_bound": round((3 * n4 + 3 * n5) * usd_trial, 2)}
    # the paper's own resolution: 4,590 trials over its conditions
    paper = {"trials": 4590, "note": "if spread over >= 17 conditions (progressive 4, perturbation 4, failure 4, "
             "composition 4, main 3 minus overlaps), about 270 trials per condition: SE of a difference ~ 4.3 pp"}
    paper["se_diff_pp_at_270"] = round(100 * math.sqrt(2 * 0.5 * 0.5 / 270), 1)
    out = {"config": {"alpha": 0.05, "power": 0.8, "usd_per_trial_lower_bound": round(usd_trial, 5),
                      "tokens_per_trial": TOKENS_PER_TRIAL, "haiku_prices_per_mtok": [pin, pout], "cap_usd": CAP_USD,
                      "preregistration": "docs/methods/evomap/claims-audit.md#retry-round-2-preregistration"},
           "tests": rows, "total": total, "paper_resolution": paper,
           "verdict": {"any_test_fits_cap": any(r["fits_cap"] for r in rows.values()),
                       "full_v4_v5_fits_cap": total["usd_lower_bound"] <= CAP_USD}}
    save("x23_power_v4_v5", out)
    import json
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
