"""X8: GDI is manipulable through self-reported metadata (blast radius dominates); SafeHub's rank is not.

Claim [snip:BE]: 35% of the ranking score is an intrinsic component driven by self-reported metadata;
blast radius is the dominant lever (degrading only blast radius drops the optimal score 40.2 -> 36.0).

Protocol: grow a hub state with a population run (per seed), then take the honest-authored assets and, one
field at a time, set the claimed value to the "optimal" configuration (blast radius 1 file / 20 lines,
confidence 0.99, success streak 5, outcome score 0.99, validation report ok) or all at once. Measure the GDI
change and the change in rank percentile among all served assets. For SafeHub: the same inflated bundles
(i) scored with the SafeHub rank directly and (ii) re-submitted by an "inflator" (as a renamed gene to evade the
exact-duplicate check). Also: degrade only blast radius from the optimum to a large diff.

Run: python experiments/evomap/x8_metadata_inflation.py [--llm sim|claude:haiku] [--seeds N] [--quick]
"""
from __future__ import annotations

import copy

from _common import (DEFAULT_MIX, agent_config, fmt, live, make_hub, make_world, parse_args, pmap,
                     population_specs, save, summarize)

import numpy as np

from rsi.evomap import Gene, PopulationSimulator
from rsi.evomap.population import inflate

FIELDS = {"blast": ("blast",), "confidence": ("confidence",), "streak": ("streak",), "score": ("score",),
          "report": ("report",), "all": ("blast", "confidence", "streak", "score", "report")}


def grow(world, hub_kind, seed, n, epochs):
    specs = population_specs(n, DEFAULT_MIX, seed, classes=world.domain.tasks.families("evolve"))
    hub = make_hub(hub_kind, world, seed=seed)
    sim = PopulationSimulator(world.domain, world.harness, hub, specs,
                              config=agent_config("naive" if hub_kind == "naive" else "safe", seed),
                              model_factory=world.model_factory, proposer_factory=world.proposer_factory,
                              forge=world.forge, truth=world.truth, seed=seed)
    sim.run(epochs)
    return hub, sim


def honest_records(sim, hub, rng, k=40):
    """Honest assets built from the agents' real genes + capsules, with claimed metadata drawn from realistic
    ranges (multi-file diffs, confidence = a process score, short streaks)."""
    from rsi.evomap import AssetRecord
    out = []
    for name, ag in sim.agents.items():
        if not name.endswith(("honest", "freerider")):
            continue
        caps = {c.gene: c for c in ag.store.capsules.values()}
        for g in ag.store.genes.values():
            if g.id in caps and not g.id.startswith("gene_auto_"):
                b = ag.make_bundle(g, caps[g.id], None, {"overall_ok": True}, {}, {})
                c = b.capsule
                c["blast_radius"] = {"files": 1 + int(rng.expovariate(1 / 2.0)),
                                     "lines": max(5, int(rng.lognormvariate(np.log(80), 0.8)))}
                c["confidence"] = round(rng.uniform(0.6, 0.95), 2)
                c["success_streak"] = 1 + int(rng.expovariate(1 / 0.7))
                c["outcome"] = {"status": "success", "score": round(rng.uniform(0.7, 0.95), 2)}
                if all(o.asset_id != b.gene.get("asset_id") for o in out):   # one record per gene content
                    out.append(AssetRecord(b.gene.get("asset_id"), b, name, "promoted", hub.epoch))
    rng.shuffle(out)
    return out[:k]


def pct(score, others):
    return float(np.mean([score > o for o in others])) if others else 0.0


def one(job):
    seed, args = job
    world = make_world(args, seed)
    n, epochs = (6, 3) if live(args) else ((20, 8) if args.quick else (30, 15))
    out = {"seed": seed}
    # ---------------- naive hub / GDI
    import random
    hub, sim = grow(world, "naive", seed, n, epochs)
    served = [r for r in hub.published() if r.status == "promoted"]
    scores = {r.asset_id: hub.ranker.score(r, hub.epoch) for r in served}
    honest = honest_records(sim, hub, random.Random(seed))
    for r in honest:
        scores[r.asset_id] = hub.ranker.score(r, hub.epoch)
    for f, fields in FIELDS.items():
        d_score, d_pct = [], []
        for r in honest:
            others = [s for a, s in scores.items() if a != r.asset_id]
            r2 = copy.deepcopy(r)
            r2.bundle = inflate(r.bundle, fields=fields)
            s0, s1 = scores[r.asset_id], hub.ranker.score(r2, hub.epoch)
            d_score.append(s1 - s0)
            d_pct.append(pct(s1, others) - pct(s0, others))
        out[f"gdi_dscore_{f}"] = float(np.mean(d_score)) if d_score else float("nan")
        out[f"gdi_dpct_{f}"] = float(np.mean(d_pct)) if d_pct else float("nan")
    # degrade blast radius only, from the optimal configuration
    deg = []
    for r in honest:
        opt = copy.deepcopy(r)
        opt.bundle = inflate(r.bundle)
        big = copy.deepcopy(opt)
        big.bundle = inflate(opt.bundle, fields=("blast",), files=8, lines=300)
        deg.append((hub.ranker.score(opt, hub.epoch), hub.ranker.score(big, hub.epoch)))
    out["gdi_optimal"] = float(np.mean([a for a, _ in deg])) if deg else float("nan")
    out["gdi_optimal_blast_degraded"] = float(np.mean([b for _, b in deg])) if deg else float("nan")
    comp = [hub.ranker.components(r, hub.epoch) for r in served]
    out["intrinsic_var_share"] = float(np.var([0.35 * c["I"] for c in comp]) /
                                       max(1e-12, np.var([hub.ranker.score(r, hub.epoch) / 100 for r in served])))
    # ---------------- SafeHub
    sh, _ = grow(world, "safe", seed, n, epochs)
    ver = [r for r in sh.published() if r.status in ("verified", "promoted")]
    for f, fields in FIELDS.items():
        d_rank, resub = [], []
        for r in ver:
            s0 = sh.rank_score(r)
            r2 = copy.deepcopy(r)
            r2.bundle = inflate(r.bundle, fields=fields)
            d_rank.append(sh.rank_score(r2) - s0)
            b = inflate(r.bundle, fields=fields)
            g = Gene.from_dict(b.gene)
            g.id = g.id + "_inflated"
            b.gene = g.stamp().to_dict()
            dec = sh.publish(b, "x8_inflator")
            resub.append(dec.status)
        out[f"safe_drank_{f}"] = float(np.mean(np.abs(d_rank))) if d_rank else float("nan")
        out[f"safe_resubmit_new_rank_share_{f}"] = float(np.mean([s in ("verified", "promoted") for s in resub])) \
            if resub else float("nan")
    out["n_naive_assets"] = len(honest)
    out["n_safe_assets"] = len(ver)
    return out


def main():
    args = parse_args(__doc__.split("\n")[0], default_seeds=10)
    rows = pmap(one, [(s, args) for s in range(args.seeds)], args.workers)
    keys = [k for k in rows[0] if k != "seed"]
    summ = {k: summarize([r[k] for r in rows]) for k in keys}
    gd = {f: summ[f"gdi_dpct_{f}"]["mean"] for f in FIELDS if f != "all"}
    out = {"config": {"llm": args.llm, "seeds": args.seeds, "fields": FIELDS}, "raw": rows, "summary": summ,
           "verdict": {"gdi_moved_by_inflation": summ["gdi_dpct_all"]["lo"] > 0,
                       "largest_single_lever": max(gd, key=lambda f: gd[f] if gd[f] is not None else -1),
                       "blast_is_largest_lever": max(gd, key=lambda f: gd[f] if gd[f] is not None else -1) == "blast",
                       "blast_degradation_drop": [summ["gdi_optimal"]["mean"], summ["gdi_optimal_blast_degraded"]["mean"]],
                       "safehub_rank_unchanged": all(summ[f"safe_drank_{f}"]["mean"] == 0 for f in FIELDS)
                       if summ["safe_drank_all"]["n"] else None,
                       "safehub_resubmissions_never_gain_rank":
                           all(summ[f"safe_resubmit_new_rank_share_{f}"]["mean"] == 0 for f in FIELDS)
                           if summ["safe_drank_all"]["n"] else None}}
    save("x8_metadata_inflation", out, args.out)
    for k in keys:
        print(f"  {k:40s} {fmt(summ[k])}")
    print("verdict:", out["verdict"])


if __name__ == "__main__":
    main()
