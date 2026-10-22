"""X8: GDI is manipulable through self-reported metadata (blast radius dominates); SafeHub's rank is not.
Run: python experiments/evomap/x8_metadata_inflation.py [--llm sim|claude:haiku] [--seeds N] [--quick]
"""
from __future__ import annotations

import copy
import random

from _common import (DEFAULT_MIX, agent_config, live, make_hub, make_world, mean_or_nan, parse_args, pmap,
                     population_specs, print_stats, save, simulator, summarize_columns)

import numpy as np

from rsi.evomap import AssetRecord, Gene
from rsi.evomap.population import inflate

FIELDS = {"blast": ("blast",), "confidence": ("confidence",), "streak": ("streak",), "score": ("score",),
          "report": ("report",), "all": ("blast", "confidence", "streak", "score", "report")}


def grow(world, hub_kind: str, seed: int, n: int, epochs: int):
    specs = population_specs(n, DEFAULT_MIX, seed, classes=world.domain.tasks.families("evolve"))
    hub = make_hub(hub_kind, world, seed=seed)
    sim = simulator(world, hub, specs, agent_config("naive" if hub_kind == "naive" else "safe", seed), seed)
    sim.run(epochs)
    return hub, sim


def honest_records(sim, hub, rng: random.Random, k: int = 40) -> list[AssetRecord]:
    """Honest assets from the agents' real genes and capsules, with claimed metadata drawn from realistic ranges."""
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


def pct(score: float, others: list[float]) -> float:
    return float(np.mean([score > o for o in others])) if others else 0.0


def inflated(r, **kw):
    r2 = copy.deepcopy(r)
    r2.bundle = inflate(r.bundle, **kw)
    return r2


def naive_gdi(world, seed: int, n: int, epochs: int) -> tuple[dict, int]:
    """GDI and rank-percentile change from inflating each claimed field of honest assets; blast-only degradation."""
    out = {}
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
            s0, s1 = scores[r.asset_id], hub.ranker.score(inflated(r, fields=fields), hub.epoch)
            d_score.append(s1 - s0)
            d_pct.append(pct(s1, others) - pct(s0, others))
        out[f"gdi_dscore_{f}"] = mean_or_nan(d_score)
        out[f"gdi_dpct_{f}"] = mean_or_nan(d_pct)
    deg = []
    for r in honest:
        opt = inflated(r)
        big = inflated(opt, fields=("blast",), files=8, lines=300)
        deg.append((hub.ranker.score(opt, hub.epoch), hub.ranker.score(big, hub.epoch)))
    out["gdi_optimal"] = mean_or_nan([a for a, _ in deg])
    out["gdi_optimal_blast_degraded"] = mean_or_nan([b for _, b in deg])
    comp = [hub.ranker.components(r, hub.epoch) for r in served]
    out["intrinsic_var_share"] = float(np.var([0.35 * c["I"] for c in comp]) /
                                       max(1e-12, np.var([hub.ranker.score(r, hub.epoch) / 100 for r in served])))
    return out, len(honest)


def safehub_rank(world, seed: int, n: int, epochs: int) -> tuple[dict, int]:
    """SafeHub rank change of the inflated bundles, scored directly and re-submitted as renamed genes."""
    out = {}
    sh, _ = grow(world, "safe", seed, n, epochs)
    ver = [r for r in sh.published() if r.status in ("verified", "promoted")]
    for f, fields in FIELDS.items():
        d_rank, resub = [], []
        for r in ver:
            s0 = sh.rank_score(r)
            d_rank.append(sh.rank_score(inflated(r, fields=fields)) - s0)
            b = inflate(r.bundle, fields=fields)
            g = Gene.from_dict(b.gene)
            g.id = g.id + "_inflated"          # evades the exact-duplicate check
            b.gene = g.stamp().to_dict()
            resub.append(sh.publish(b, "x8_inflator").status)
        out[f"safe_drank_{f}"] = float(np.mean(np.abs(d_rank))) if d_rank else float("nan")
        out[f"safe_resubmit_new_rank_share_{f}"] = mean_or_nan([s in ("verified", "promoted") for s in resub])
    return out, len(ver)


def one(job) -> dict:
    seed, args = job
    world = make_world(args, seed)
    n, epochs = (6, 3) if live(args) else ((20, 8) if args.quick else (30, 15))
    naive, n_naive = naive_gdi(world, seed, n, epochs)
    safe, n_safe = safehub_rank(world, seed, n, epochs)
    return {"seed": seed, **naive, **safe, "n_naive_assets": n_naive, "n_safe_assets": n_safe}


def main():
    args = parse_args(__doc__.split("\n")[0], default_seeds=10)
    rows = pmap(one, [(s, args) for s in range(args.seeds)], args.workers)
    summ = summarize_columns(rows)
    gd = {f: summ[f"gdi_dpct_{f}"]["mean"] for f in FIELDS if f != "all"}
    largest = max(gd, key=lambda f: gd[f] if gd[f] is not None else -1)
    safe_n = summ["safe_drank_all"]["n"]
    out = {"config": {"llm": args.llm, "seeds": args.seeds, "fields": FIELDS}, "raw": rows, "summary": summ,
           "verdict": {"gdi_moved_by_inflation": summ["gdi_dpct_all"]["lo"] > 0,
                       "largest_single_lever": largest,
                       "blast_is_largest_lever": largest == "blast",
                       "blast_degradation_drop": [summ["gdi_optimal"]["mean"],
                                                  summ["gdi_optimal_blast_degraded"]["mean"]],
                       "safehub_rank_unchanged": all(summ[f"safe_drank_{f}"]["mean"] == 0 for f in FIELDS)
                       if safe_n else None,
                       "safehub_resubmissions_never_gain_rank":
                           all(summ[f"safe_resubmit_new_rank_share_{f}"]["mean"] == 0 for f in FIELDS)
                           if safe_n else None}}
    save("x8_metadata_inflation", out, args.out)
    print_stats(summ, 40)
    print("verdict:", out["verdict"])


if __name__ == "__main__":
    main()
