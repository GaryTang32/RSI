"""X7 (+X16): replicate the "Behind EvoMap" dynamics on a publish-rewarding, self-reported hub.

Claim [abs:BE]: 98% of assets are never reused; credits concentrate in ~10% of agents who mass-publish;
>84% of approved assets pass validation with vacuous tests; the GDI ranking is dominated by self-reported
metadata (so it is weakly related to what assets actually do).

Setup: a GeneWorld population (40 agents: 60% honest faithful-Evolver agents, 15% farmers, 10% inflators,
10% freeriders, 5% poisoners; heterogeneous abilities) on :class:`NaiveEvoMapHub` for 30 epochs.
Arms: ``base``; ``no_farmers`` (farmers replaced by honest agents: which findings need farmers?);
``real_validation`` (honest gene writers always attach the real check instead of following the distiller's
"prefer --version" advice: which findings need that advice?).
X16 from the same cycles: Spearman(composite process score, graded task outcome) and the share of
vacuous/skipped-validation cycles that still reach the 0.78 publish bar.

Run: python experiments/evomap/x7_behind_evomap.py [--llm sim|claude:haiku] [--seeds N] [--quick]
"""
from __future__ import annotations

import math

from _common import (DEFAULT_MIX, agent_config, fmt, live, make_hub, make_world, paired, parse_args,
                     pmap, population_specs, save, summarize)

import numpy as np

from rsi.core.stats import spearman
from rsi.evomap import PopulationSimulator

ARMS = {"base": {}, "no_farmers": {"mix": {**DEFAULT_MIX, "farmer": 0.0, "honest": 0.75}},
        "real_validation": {"p_real": 1.0}}


def one(job):
    seed, arm, args = job
    spec = ARMS[arm]
    world = make_world(args, seed, p_real_validation=spec.get("p_real", 0.3))
    n, epochs = (6, 3) if live(args) else ((24, 10) if args.quick else (40, 30))
    specs = population_specs(n, spec.get("mix", DEFAULT_MIX), seed, classes=world.domain.tasks.families("evolve"))
    hub = make_hub("naive", world)
    sim = PopulationSimulator(world.domain, world.harness, hub, specs, config=agent_config("naive", seed),
                              model_factory=world.model_factory, proposer_factory=world.proposer_factory,
                              forge=world.forge, truth=world.truth, seed=seed)
    s = sim.run(epochs)
    h = s["hub"]
    # X16: composite vs graded task outcome over honest agents' cycles
    rows = [r for r in sim.cycles if "composite" in r and r["kind"] in ("honest", "inflator", "freerider")]
    comp = [r["composite"] for r in rows]
    task = [r["task_score"] for r in rows]
    vac_rows, real_rows = [], []
    for r in rows:
        g = sim.agents[r["agent"]].store.genes.get(r["gene_id"]) if r["gene_id"] else None
        if g is None:
            continue
        vac = r["n_validation_run"] == 0 or any("--version" in v for v in g.validation)
        (vac_rows if vac else real_rows).append(r)
    pub_share = float(np.mean([r["composite"] >= 0.78 for r in vac_rows])) if vac_rows else float("nan")

    def rho(rs):
        if len(rs) < 3 or len({r["composite"] for r in rs}) < 2 or len({r["task_score"] for r in rs}) < 2:
            return float("nan")
        return spearman([r["composite"] for r in rs], [r["task_score"] for r in rs])
    earned = hub.credits.earned_all([sp.name for sp in specs])
    top = sorted(earned, key=lambda a: -earned[a])[: max(1, math.ceil(0.1 * len(specs)))]
    return {"seed": seed, "arm": arm, "never_reused_published": h["never_reused_published"],
            "never_reused_promoted": h["never_reused_promoted"], "promotion_rate": h["promotion_rate"],
            "n_published": h["n_published"], "credit_top10_share": h["credit_top10_share"],
            "credit_gini": h["credit_gini"], "farmer_credit_share": s["credit_share_by_kind"].get("farmer", 0.0),
            "top10_are_farmers": float(np.mean([a.endswith("farmer") for a in top])),
            "vacuous_share_promoted": h["vacuous_share_promoted"], "rank_validity": h["rank_validity"], "surfacing_validity": h["surfacing_validity"],
            "served_true_effect": h["served_true_effect"],
            "consumer_uplift_true": h.get("consumer_uplift_true", float("nan")),
            "credits_from_promotion": h["credits_by_reason"].get("promotion", 0.0) /
            max(1e-9, sum(v for k, v in h["credits_by_reason"].items() if k in ("promotion", "fetch"))),
            "solve_rate": s["solve_rate"], "x16_spearman_composite_task": spearman(comp, task) if len(comp) > 2 else
            float("nan"), "x16_spearman_vacuous_cycles": rho(vac_rows), "x16_spearman_real_cycles": rho(real_rows),
            "x16_vacuous_cycles_publishable": pub_share,
            "x16_vacuous_failed_task_publishable": float(np.mean([r["composite"] >= 0.78 for r in vac_rows
                                                                 if not r["task_success"]]))
            if any(not r["task_success"] for r in vac_rows) else float("nan"),
            "x16_n_vacuous_cycles": len(vac_rows), "x16_n_real_cycles": len(real_rows)}


METRICS = ["never_reused_published", "never_reused_promoted", "promotion_rate", "n_published", "credit_top10_share",
           "credit_gini", "farmer_credit_share", "top10_are_farmers", "credits_from_promotion",
           "vacuous_share_promoted", "rank_validity", "surfacing_validity", "served_true_effect", "consumer_uplift_true", "solve_rate",
           "x16_spearman_composite_task", "x16_spearman_vacuous_cycles", "x16_spearman_real_cycles",
           "x16_vacuous_cycles_publishable", "x16_vacuous_failed_task_publishable", "x16_n_vacuous_cycles",
           "x16_n_real_cycles"]


def main():
    args = parse_args(__doc__.split("\n")[0], default_seeds=20)
    arms = ["base"] if (args.quick or live(args)) else list(ARMS)
    jobs = [(s, a, args) for a in arms for s in range(args.seeds)]
    rows = pmap(one, jobs, args.workers)
    out = {"config": {"llm": args.llm, "seeds": args.seeds, "arms": arms, "mix": DEFAULT_MIX,
                      "population": "40 agents x 30 epochs" if not (args.quick or live(args)) else "reduced"},
           "raw": rows, "summary": {}}
    for arm in arms:
        rs = [r for r in rows if r["arm"] == arm]
        out["summary"][arm] = {m: summarize([r[m] for r in rs]) for m in METRICS}
    b = out["summary"]["base"]
    v = {
        "never_reused_gt_0.9": b["never_reused_published"]["mean"] > 0.9,
        "credits_concentrated_top10_gt_0.5": b["credit_top10_share"]["mean"] > 0.5,
        "credits_mostly_from_publishing": b["credits_from_promotion"]["mean"] > 0.5,
        "vacuous_share_promoted_gt_0.5": b["vacuous_share_promoted"]["mean"] > 0.5,
        "rank_weakly_related_abs_lt_0.3": abs(b["rank_validity"]["mean"]) < 0.3 if b["rank_validity"]["n"] else None,
        "x16_composite_not_task_gain_abs_rho_lt_0.3": abs(b["x16_spearman_composite_task"]["mean"]) < 0.3,
        "x16_vacuous_rho": b["x16_spearman_vacuous_cycles"]["mean"], "x16_real_rho": b["x16_spearman_real_cycles"]["mean"],
        "x16_failed_tasks_still_publishable_share": b["x16_vacuous_failed_task_publishable"]["mean"],
    }
    if "no_farmers" in out["summary"]:
        nf = out["summary"]["no_farmers"]
        v["never_reused_without_farmers"] = nf["never_reused_published"]["mean"]
        v["vacuous_without_farmers"] = nf["vacuous_share_promoted"]["mean"]
        rv = out["summary"]["real_validation"]
        v["vacuous_with_real_validation_writers"] = rv["vacuous_share_promoted"]["mean"]
        v["paired_never_reused_base_minus_nofarmers"] = paired(
            [r["never_reused_published"] for r in rows if r["arm"] == "no_farmers"],
            [r["never_reused_published"] for r in rows if r["arm"] == "base"])
    out["verdict"] = v
    save("x7_behind_evomap", out, args.out)
    for arm in arms:
        print(f"== {arm}")
        for m in METRICS:
            print(f"  {m:34s} {fmt(out['summary'][arm][m])}")
    print("verdict:", v)


if __name__ == "__main__":
    main()
