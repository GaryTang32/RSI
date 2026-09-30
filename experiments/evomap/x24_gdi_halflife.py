"""X24 (review response to retry round 2, P1c): does B9's result depend on the unknown freshness half-life?

Claim [snip:BE] (B9): the four-dimensional GDI "collapses into a one-dimensional metric dominated by the Intrinsic
component". X18 (P1 / P1b) tested h in {10, 30, 90} epochs and failed at all three. No source gives the hub's
freshness half-life, and the review of retry round 2 showed on seeds 0-1 that the no-review, no-fetch bulk's
intrinsic share rises with h. P1c (preregistered before this script was run on 12 seeds) re-runs X18's population
unchanged (same seeds, same code path; the hub ranks with its default h = 10 during the run) and recomputes the
decomposition at the crawl for h in {180, 365, inf} (inf = constant freshness), on (i) all promoted assets (P1's
metric) and (ii) the promoted assets with zero reviews and zero non-author fetches (P1b's metric).

Pass rule at each h (as P1 / P1b): intrinsic variance share CI lower bound > 0.5 and intrinsic the largest share.

Run: python experiments/evomap/x24_gdi_halflife.py [--seeds 12] [--workers 2] [--quick]
"""
from __future__ import annotations

import json

from _common import fmt, parse_args, pmap, save, summarize

import x18_gdi_protocol as x18

HALF_LIVES = (180, 365, float("inf"))


def tag(h):
    return "inf" if h == float("inf") else str(int(h))


def one(job):
    x18.HALF_LIVES = HALF_LIVES
    row = x18.one(job)
    keep = {"seed", "n_promoted", "n_quiet"}
    return {k: v for k, v in row.items()
            if k in keep or any(k.startswith(p) for p in ("h180_", "h365_", "hinf_", "quiet_h180_", "quiet_h365_",
                                                          "quiet_hinf_"))}


def main():
    args = parse_args(__doc__.split("\n")[0], default_seeds=12)
    rows = pmap(one, [(s, args) for s in range(args.seeds)], args.workers)
    keys = [k for k in rows[0] if k != "seed" and not k.endswith("_largest")]
    summ = {k: summarize([r[k] for r in rows]) for k in keys}
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
    for k in keys:
        print(f"  {k:40s} {fmt(summ[k])}")
    print(json.dumps(v, indent=1, default=str))


if __name__ == "__main__":
    main()
