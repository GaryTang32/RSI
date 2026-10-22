"""X24 (review response to retry round 2, P1c): does B9's result depend on the unknown freshness half-life?
Run: python experiments/evomap/x24_gdi_halflife.py [--seeds 12] [--workers 2] [--quick]
"""
from __future__ import annotations

import json

from _common import parse_args, pmap, print_stats, save, summarize_columns

import x18_gdi_protocol as x18

HALF_LIVES = (180, 365, float("inf"))      # inf = constant freshness
KEEP = {"seed", "n_promoted", "n_quiet"}
PREFIXES = ("h180_", "h365_", "hinf_", "quiet_h180_", "quiet_h365_", "quiet_hinf_")


def tag(h: float) -> str:
    return "inf" if h == float("inf") else str(int(h))


def one(job) -> dict:
    """X18's population unchanged (the hub ranks with h = 10 during the run), decomposed at the new half-lives."""
    x18.HALF_LIVES = HALF_LIVES
    return {k: v for k, v in x18.one(job).items() if k in KEEP or k.startswith(PREFIXES)}


def main():
    args = parse_args(__doc__.split("\n")[0], default_seeds=12)
    rows = pmap(one, [(s, args) for s in range(args.seeds)], args.workers)
    summ = summarize_columns(rows)
    v = {}
    for pre, label in (("", "all_promoted"), ("quiet_", "quiet_bulk")):
        for h in HALF_LIVES:
            p = f"{pre}h{tag(h)}"
            big = max("IUSF", key=lambda k: summ[f"{p}_var_share_{k}"]["mean"])
            v[f"{label}_h{tag(h)}"] = {
                "intrinsic_var_share": summ[f"{p}_var_share_I"], "largest_by_mean": big,
                "largest_by_seed": {k: sum(r[f"{p}_largest"] == k for r in rows) for k in "IUSF"},
                "intrinsic_level_share": summ[f"{p}_level_share_I"], "spearman_gdi_I": summ[f"{p}_spearman_gdi_I"],
                "pass": bool(summ[f"{p}_var_share_I"]["lo"] > 0.5 and big == "I")}
        v[f"{label}_n_pass"] = sum(v[f"{label}_h{tag(h)}"]["pass"] for h in HALF_LIVES)
    v["B9_verdict_P1c"] = ("PARTIAL" if v["quiet_bulk_n_pass"] >= 1 else "NOT REPRODUCED")
    v["note"] = ("P1 (h 10/30/90, all promoted) failed, so REPRODUCED is out of reach; P1c maps a quiet-bulk pass at "
                 ">= 1 new h to PARTIAL (the collapse holds only for the no-vote bulk and only under a slow freshness "
                 "decay that no source fixes)")
    out = {"config": {"seeds": args.seeds, "half_lives": [tag(h) for h in HALF_LIVES], "population": "as X18",
                      "preregistration": "docs/methods/evomap/claims-audit.md#retry-round-2-review-response"},
           "raw": rows, "summary": summ, "verdict": v}
    save("x24_gdi_halflife", out, args.out)
    print_stats(summ, 40)
    print(json.dumps(v, indent=1, default=str))


if __name__ == "__main__":
    main()
