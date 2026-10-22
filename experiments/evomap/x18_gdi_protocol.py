"""X18 (retry round 2, P1 / P1f / P2 / P4): the Behind-EvoMap GDI analyses, replicated with the study's own method.
Run: python experiments/evomap/x18_gdi_protocol.py [--seeds 12] [--workers 2] [--quick]
"""
from __future__ import annotations

import json

from _common import (DEFAULT_MIX, agent_config, make_hub, make_world, paired, parse_args, pmap, population_specs,
                     print_stats, save, simulator, summarize_columns)

import numpy as np

from rsi.core.stats import spearman

FIELDS = ("confidence", "streak", "blast", "trigger", "summary")
HALF_LIVES = (10, 30, 90)      # read at call time: X24 overrides it
FARM_RATE = 8


def decomposition(hub, served, h) -> dict:
    """P1 (B9): each component's share of the cross-asset GDI variance and mean level at freshness half-life h."""
    r = hub.ranker
    comp = [r.components(x, hub.epoch, half_life=h) for x in served]
    tot = np.array([sum(w * c[k] for k, w in zip("IUSF", r.w)) for c in comp])
    out = {}
    for k, w in zip("IUSF", r.w):
        v = np.array([w * c[k] for c in comp])
        out[f"var_share_{k}"] = float(np.var(v) / np.var(tot)) if np.var(tot) > 0 else float("nan")
        out[f"level_share_{k}"] = float(np.mean(v / tot))
    out["largest"] = max("IUSF", key=lambda k: out[f"var_share_{k}"])
    out["spearman_gdi_I"] = float(_spearman(tot, [c["I"] for c in comp]))
    return out


def _spearman(a, b) -> float:
    return spearman(list(a), list(b)) if len(set(a)) > 1 and len(set(b)) > 1 else float("nan")


def config_table(hub, served) -> dict:
    """P2 (B7): the study's Median / Worst / Optimal table and one-field-at-a-time ablations (GDI on 0-100)."""
    r = hub.ranker
    comps = [r.components(x, hub.epoch) for x in served]
    per = {f: [c[f] for c in comps] for f in FIELDS}
    U, S, F = (float(np.median([c[k] for c in comps])) for k in "USF")
    rep = 0.5                                              # R = 50 for every node

    def gdi(vals: dict) -> float:
        I = (sum(vals[f] for f in FIELDS) + rep) / 6.0
        return 100.0 * (r.w[0] * I + r.w[1] * U + r.w[2] * S + r.w[3] * F)
    med = {f: float(np.median(per[f])) for f in FIELDS}
    worst = {f: float(np.min(per[f])) for f in FIELDS}
    opt = {f: float(np.max(per[f])) for f in FIELDS}
    out = {"gdi_median": gdi(med), "gdi_worst": gdi(worst), "gdi_optimal": gdi(opt),
           **{f"norm_{k}_{f}": d[f] for k, d in (("median", med), ("worst", worst), ("optimal", opt)) for f in FIELDS}}
    for f in FIELDS:
        out[f"drop_{f}"] = gdi(opt) - gdi({**opt, f: worst[f]})
    out["share_optimal_blast"] = float(np.mean([v >= opt["blast"] for v in per["blast"]]))
    out["share_full_optimal"] = float(np.mean([all(r.components(x, hub.epoch)[f] >= opt[f] for f in FIELDS)
                                               for x in served]))
    return out


def one(job) -> dict:
    seed, args = job
    world = make_world(args, seed)
    n, epochs = (24, 12) if args.quick else (40, 40)
    specs = population_specs(n, DEFAULT_MIX, seed, classes=world.domain.tasks.families("evolve"), farm_rate=FARM_RATE)
    hub = make_hub("naive", world)
    h = simulator(world, hub, specs, agent_config("naive", seed), seed).run(epochs)["hub"]
    served = [x for x in hub.published() if x.status == "promoted"]
    quiet = [x for x in served if not x.reviews and not any(c != x.author for c, _ in x.fetches)]
    row = {"seed": seed, "n_published": h["n_published"], "n_promoted": len(served), "n_quiet": len(quiet)}
    for hl in HALF_LIVES:
        row.update({f"h{hl}_{k}": v for k, v in decomposition(hub, served, hl).items()})
        row.update({f"quiet_h{hl}_{k}": v for k, v in decomposition(hub, quiet, hl).items()})   # P1b
    row.update(config_table(hub, served))
    fetch_counts = [len({c for c, _ in x.fetches if c != x.author}) for x in served]
    row.update({
        "fid_zero_review_share": float(np.mean([len(x.reviews) == 0 for x in served])),
        "fid_never_reused_published": h["never_reused_published"],
        "fid_age_lt30_share": float(np.mean([hub.epoch - x.epoch < 30 for x in hub.published()])),
        "fid_zero_fetch_share": float(np.mean([c == 0 for c in fetch_counts])),
        "fid_fetch_p99": float(np.percentile(fetch_counts, 99)),
        "fid_fetch_mean": float(np.mean(fetch_counts)),
        "b1_never_reused_published": h["never_reused_published"],
        "b3_credit_top10_share": h["credit_top10_share"],
        "b4_trivial_command_share_promoted": h["trivial_command_share_promoted"],
    })
    return row


def b9_verdict(v: dict, label: str) -> str:
    n = sum(v[f"{label}{hl}"]["pass"] for hl in HALF_LIVES)
    return "REPRODUCED" if n == len(HALF_LIVES) else ("PARTIAL" if n else "NOT REPRODUCED")


def main():
    args = parse_args(__doc__.split("\n")[0], default_seeds=12)
    rows = pmap(one, [(s, args) for s in range(args.seeds)], args.workers)
    summ = summarize_columns(rows)

    def largest(prefix):
        return max("IUSF", key=lambda k: summ[f"{prefix}_var_share_{k}"]["mean"])

    def passes(prefix):
        return bool(summ[f"{prefix}_var_share_I"]["lo"] > 0.5 and largest(prefix) == "I")
    v = {}
    for hl in HALF_LIVES:
        by_seed = [r[f"h{hl}_largest"] for r in rows]
        v[f"B9_h{hl}"] = {"intrinsic_var_share": summ[f"h{hl}_var_share_I"],
                          "largest_component_by_seed": {k: by_seed.count(k) for k in "IUSF"},
                          "largest_by_mean": largest(f"h{hl}"), "pass": passes(f"h{hl}")}
    v["B9_verdict"] = b9_verdict(v, "B9_h")
    for hl in HALF_LIVES:                                  # P1b: the zero-review, zero-fetch bulk
        v[f"B9b_quiet_h{hl}"] = {"intrinsic_var_share": summ[f"quiet_h{hl}_var_share_I"],
                                 "largest_by_mean": largest(f"quiet_h{hl}"), "pass": passes(f"quiet_h{hl}")}
    v["B9b_verdict"] = b9_verdict(v, "B9b_quiet_h")
    drops = {f: summ[f"drop_{f}"]["mean"] for f in FIELDS}
    order = sorted(FIELDS, key=lambda f: -drops[f])
    second = order[1] if order[0] == "blast" else order[0]
    pd = paired([r[f"drop_{second}"] for r in rows], [r["drop_blast"] for r in rows])
    b7 = v["B7"] = {"drops_mean": drops, "order": order, "blast_minus_next": pd,
                    "blast_largest": order[0] == "blast",
                    "conf_le_half_blast": drops["confidence"] <= 0.5 * drops["blast"],
                    "streak_le_half_blast": drops["streak"] <= 0.5 * drops["blast"],
                    "gdi_median_worst_optimal": [summ[k]["mean"] for k in ("gdi_median", "gdi_worst", "gdi_optimal")],
                    "paper": {"gdi_median": 38.6, "gdi_optimal": 40.2, "gdi_optimal_blast_degraded": 36.0}}
    b7["pass"] = bool(b7["blast_largest"] and pd["lo"] > 0 and b7["conf_le_half_blast"] and b7["streak_le_half_blast"])
    v["B7_verdict"] = "REPRODUCED" if b7["pass"] else "NOT REPRODUCED"
    v["P1f_fidelity"] = {"zero_review_share": (summ["fid_zero_review_share"]["mean"], 0.99),
                         "never_reused": (summ["fid_never_reused_published"]["mean"], 0.98),
                         "age_lt30_share": (summ["fid_age_lt30_share"]["mean"], 0.73),
                         "fetch_p99": (summ["fid_fetch_p99"]["mean"], 3.0),
                         "zero_fetch_share": summ["fid_zero_fetch_share"]["mean"]}
    v["P4"] = {"never_reused": (summ["b1_never_reused_published"], 0.98),
               "credit_top10_share": (summ["b3_credit_top10_share"], "> 0.5"),
               "trivial_command_share": (summ["b4_trivial_command_share_promoted"], 0.84)}
    out = {"config": {"seeds": args.seeds, "population": "24x12 (quick)" if args.quick else "40 agents x 40 epochs",
                      "mix": DEFAULT_MIX, "farm_rate": FARM_RATE, "gdi": "be2026", "half_lives": HALF_LIVES,
                      "preregistration": "docs/methods/evomap/claims-audit.md#retry-round-2-preregistration"},
           "raw": rows, "summary": summ, "verdict": v}
    save("x18_gdi_protocol", out, args.out)
    print_stats(summ, 40)
    print(json.dumps(v, indent=1, default=str))


if __name__ == "__main__":
    main()
