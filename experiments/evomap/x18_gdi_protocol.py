"""X18 (retry round 2, P1 / P1f / P2 / P4): the Behind-EvoMap GDI analyses, replicated with the study's own method.

Claims [snip:BE]: B9 the four-dimensional GDI "collapses into a one-dimensional metric dominated by the Intrinsic
component" (99 % of assets have zero votes, 73 % were created within 30 days of the crawl); B7 blast radius is the
single most influential signal: "degrading only s_blast reduces the optimal score sharply from 40.2 to 36.0",
confidence and streak have minimal effect ("the top rows present the baseline configurations (Median, Worst, and
Optimal), while the bottom ablation rows degrade one metadata field from its optimal to worst value at a time";
"inflating metadata to the Optimal setup raises the GDI from the Median 38.6 to 40.2").

Setup (preregistered in docs/methods/evomap/claims-audit.md, "Retry round 2: preregistration"): X7's base
population (40 agents, 15 % farmers publishing 8 assets per epoch) on the naive hub with the study's intrinsic formula
(``GDIRanker(variant="be2026")``, the default), for 40 epochs (1 epoch = 1 simulated day: about 75 % of assets are
younger than 30 days at the crawl, the paper's 73 %). At the end ("the crawl"), over promoted assets:

* P1 (B9): each component's share of the cross-asset GDI variance, var(w_k * c_k) / var(GDI), with the freshness
  half-life recomputed at h in {10, 30, 90} epochs;
* P1f: population fidelity numbers next to the paper's (zero-review share, never-reused share, age < 30 share,
  non-author fetch counts);
* P2 (B7): the paper's configuration table. For each controllable field (confidence, streak, blast F*L, trigger
  count, summary length) its normalized value at Median / Worst (min) / Optimal (max) over the promoted capsules;
  a probe asset gets U, S, F at the population medians (h = 10) and R = 50; drop_f = GDI(Optimal) - GDI(Optimal
  with f at Worst);
* P4: never-reused share, top-10 % earned-credit share, trivial-command share of promoted (B1, B3, B4 magnitudes).

Run: python experiments/evomap/x18_gdi_protocol.py [--seeds 12] [--workers 2] [--quick]
"""
from __future__ import annotations

from _common import (DEFAULT_MIX, agent_config, fmt, make_hub, make_world, paired, parse_args, pmap,
                     population_specs, save, summarize)

import numpy as np

from rsi.evomap import PopulationSimulator

FIELDS = ("confidence", "streak", "blast", "trigger", "summary")
HALF_LIVES = (10, 30, 90)
FARM_RATE = 8


def decomposition(hub, served, h):
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


def _spearman(a, b):
    from rsi.core.stats import spearman
    return spearman(list(a), list(b)) if len(set(a)) > 1 and len(set(b)) > 1 else float("nan")


def config_table(hub, served):
    """P2: the study's Median / Worst / Optimal table and one-field-at-a-time ablations (GDI on the 0-100 scale)."""
    r = hub.ranker
    per = {f: [] for f in FIELDS}
    rest = {"I_rep": [], "U": [], "S": [], "F": []}
    for x in served:
        c = r.components(x, hub.epoch)
        for f in FIELDS:
            per[f].append(c[f])
        rest["I_rep"].append(c["reputation"])
        for k in "USF":
            rest[k].append(c[k])
    U, S, F = (float(np.median(rest[k])) for k in "USF")
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


def one(job):
    seed, args = job
    world = make_world(args, seed)
    n, epochs = (24, 12) if args.quick else (40, 40)
    specs = population_specs(n, DEFAULT_MIX, seed, classes=world.domain.tasks.families("evolve"), farm_rate=FARM_RATE)
    hub = make_hub("naive", world)
    sim = PopulationSimulator(world.domain, world.harness, hub, specs, config=agent_config("naive", seed),
                              model_factory=world.model_factory, proposer_factory=world.proposer_factory,
                              forge=world.forge, truth=world.truth, seed=seed)
    s = sim.run(epochs)
    h = s["hub"]
    served = [x for x in hub.published() if x.status == "promoted"]
    row = {"seed": seed, "n_published": h["n_published"], "n_promoted": len(served)}
    quiet = [x for x in served if not x.reviews and not any(c != x.author for c, _ in x.fetches)]
    row["n_quiet"] = len(quiet)
    for hl in HALF_LIVES:
        for k, v in decomposition(hub, served, hl).items():
            row[f"h{hl}_{k}"] = v
        for k, v in decomposition(hub, quiet, hl).items():          # P1b: zero reviews and zero fetches
            row[f"quiet_h{hl}_{k}"] = v
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


def main():
    args = parse_args(__doc__.split("\n")[0], default_seeds=12)
    rows = pmap(one, [(s, args) for s in range(args.seeds)], args.workers)
    keys = [k for k in rows[0] if k not in ("seed",) and not k.endswith("_largest")]
    summ = {k: summarize([r[k] for r in rows]) for k in keys}
    v = {}
    for hl in HALF_LIVES:
        largest = [r[f"h{hl}_largest"] for r in rows]
        v[f"B9_h{hl}"] = {"intrinsic_var_share": summ[f"h{hl}_var_share_I"],
                          "largest_component_by_seed": {k: largest.count(k) for k in "IUSF"},
                          "largest_by_mean": max("IUSF", key=lambda k: summ[f"h{hl}_var_share_{k}"]["mean"]),
                          "pass": bool(summ[f"h{hl}_var_share_I"]["lo"] > 0.5 and
                                       max("IUSF", key=lambda k: summ[f"h{hl}_var_share_{k}"]["mean"]) == "I")}
    npass = sum(v[f"B9_h{hl}"]["pass"] for hl in HALF_LIVES)
    v["B9_verdict"] = "REPRODUCED" if npass == len(HALF_LIVES) else ("PARTIAL" if npass else "NOT REPRODUCED")
    for hl in HALF_LIVES:                                             # P1b (follow-up, reported next to P1)
        big = max("IUSF", key=lambda k: summ[f"quiet_h{hl}_var_share_{k}"]["mean"])
        v[f"B9b_quiet_h{hl}"] = {"intrinsic_var_share": summ[f"quiet_h{hl}_var_share_I"], "largest_by_mean": big,
                                 "pass": bool(summ[f"quiet_h{hl}_var_share_I"]["lo"] > 0.5 and big == "I")}
    nq = sum(v[f"B9b_quiet_h{hl}"]["pass"] for hl in HALF_LIVES)
    v["B9b_verdict"] = "REPRODUCED" if nq == len(HALF_LIVES) else ("PARTIAL" if nq else "NOT REPRODUCED")
    drops = {f: summ[f"drop_{f}"]["mean"] for f in FIELDS}
    order = sorted(FIELDS, key=lambda f: -drops[f])
    second = order[1] if order[0] == "blast" else order[0]
    pd = paired([r[f"drop_{second}"] for r in rows], [r["drop_blast"] for r in rows])
    v["B7"] = {"drops_mean": drops, "order": order, "blast_minus_next": pd,
               "blast_largest": order[0] == "blast",
               "conf_le_half_blast": drops["confidence"] <= 0.5 * drops["blast"],
               "streak_le_half_blast": drops["streak"] <= 0.5 * drops["blast"],
               "gdi_median_worst_optimal": [summ[k]["mean"] for k in ("gdi_median", "gdi_worst", "gdi_optimal")],
               "paper": {"gdi_median": 38.6, "gdi_optimal": 40.2, "gdi_optimal_blast_degraded": 36.0}}
    v["B7"]["pass"] = bool(v["B7"]["blast_largest"] and pd["lo"] > 0 and v["B7"]["conf_le_half_blast"]
                           and v["B7"]["streak_le_half_blast"])
    v["B7_verdict"] = "REPRODUCED" if v["B7"]["pass"] else "NOT REPRODUCED"
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
    for k in keys:
        print(f"  {k:40s} {fmt(summ[k])}")
    import json
    print(json.dumps(v, indent=1, default=str))


if __name__ == "__main__":
    main()
