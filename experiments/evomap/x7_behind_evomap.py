"""X7 (+X16): replicate the "Behind EvoMap" dynamics on a publish-rewarding, self-reported hub.
Run: python experiments/evomap/x7_behind_evomap.py [--llm sim|claude:haiku] [--seeds N] [--quick]
"""
from __future__ import annotations

import math

from _common import (DEFAULT_MIX, agent_config, by_arm, live, make_hub, make_world, mean_or_nan, paired, parse_args,
                     pmap, population_specs, print_groups, save, simulator, summarize_groups)

import numpy as np

from rsi.core.stats import spearman

ARMS = {"base": {}, "no_farmers": {"mix": {**DEFAULT_MIX, "farmer": 0.0, "honest": 0.75}},
        "real_validation": {"p_real": 1.0}, "farm_rate_2": {"farm_rate": 2}, "farm_rate_1": {"farm_rate": 1}}
FARM_RATE = 8
PUBLISH_BAR = 0.78
METRICS = ["never_reused_published", "never_reused_promoted", "promotion_rate", "n_published", "credit_top10_share",
           "credit_gini", "farmer_credit_share", "top10_are_farmers", "credits_from_promotion",
           "vacuous_share_promoted", "trivial_command_share_promoted", "n_promoted", "zero_review_share_promoted",
           *[f"gdi_{a}_share_{k}" for a in ("level", "var") for k in "IUSF"],
           "rank_validity", "surfacing_validity", "served_true_effect", "consumer_uplift_true", "solve_rate",
           "x16_spearman_composite_task", "x16_spearman_vacuous_cycles", "x16_spearman_real_cycles",
           "x16_vacuous_cycles_publishable", "x16_vacuous_failed_task_publishable", "x16_n_vacuous_cycles",
           "x16_n_real_cycles"]
FARM_RATE_METRICS = ("never_reused_published", "vacuous_share_promoted", "trivial_command_share_promoted",
                     "credit_top10_share", "zero_review_share_promoted", "gdi_var_share_I")


def gdi_decomposition(hub) -> dict:
    """Per GDI component over promoted assets: its share of the mean GDI, of the GDI variance, and its correlation."""
    served = [r for r in hub.published() if r.status == "promoted"]
    out = {"zero_review_share_promoted": mean_or_nan([len(r.reviews) == 0 for r in served])}
    if len(served) < 3:
        return out
    comp = [hub.ranker.components(r, hub.epoch) for r in served]
    gdi = np.array([hub.ranker.score(r, hub.epoch) / 100 for r in served])
    for k, w in zip("IUSF", hub.ranker.w):
        v = np.array([w * c[k] for c in comp])
        out[f"gdi_level_share_{k}"] = float(np.mean(v / gdi))
        out[f"gdi_var_share_{k}"] = float(np.var(v) / np.var(gdi)) if np.var(gdi) > 0 else float("nan")
        out[f"gdi_corr_{k}"] = float(np.corrcoef(v, gdi)[0, 1]) if np.std(v) > 0 and np.std(gdi) > 0 else float("nan")
    return out


def _rho(rows: list[dict]) -> float:
    if len(rows) < 3 or len({r["composite"] for r in rows}) < 2 or len({r["task_score"] for r in rows}) < 2:
        return float("nan")
    return spearman([r["composite"] for r in rows], [r["task_score"] for r in rows])


def one(job) -> dict:
    seed, arm, args = job
    spec = ARMS[arm]
    world = make_world(args, seed, p_real_validation=spec.get("p_real", 0.3))
    n, epochs = (6, 3) if live(args) else ((24, 10) if args.quick else (40, 30))
    specs = population_specs(n, spec.get("mix", DEFAULT_MIX), seed, classes=world.domain.tasks.families("evolve"),
                             farm_rate=spec.get("farm_rate", FARM_RATE))
    hub = make_hub("naive", world)
    sim = simulator(world, hub, specs, agent_config("naive", seed), seed)
    s = sim.run(epochs)
    h = s["hub"]
    # X16: composite process score vs graded task outcome, split by vacuous (or skipped) vs real validation
    rows = [r for r in sim.cycles if "composite" in r and r["kind"] in ("honest", "inflator", "freerider")]
    vac_rows, real_rows = [], []
    for r in rows:
        g = sim.agents[r["agent"]].store.genes.get(r["gene_id"]) if r["gene_id"] else None
        if g is None:
            continue
        vac = r["n_validation_run"] == 0 or any("--version" in v for v in g.validation)
        (vac_rows if vac else real_rows).append(r)
    earned = hub.credits.earned_all([sp.name for sp in specs])
    top = sorted(earned, key=lambda a: -earned[a])[: max(1, math.ceil(0.1 * len(specs)))]
    reasons = h["credits_by_reason"]
    return {"seed": seed, "arm": arm,
            **{k: h[k] for k in ("never_reused_published", "never_reused_promoted", "promotion_rate", "n_published",
                                 "credit_top10_share", "credit_gini")},
            "farmer_credit_share": s["credit_share_by_kind"].get("farmer", 0.0),
            "top10_are_farmers": float(np.mean([a.endswith("farmer") for a in top])),
            **{k: h[k] for k in ("vacuous_share_promoted", "trivial_command_share_promoted", "n_promoted")},
            **gdi_decomposition(hub),
            **{k: h[k] for k in ("rank_validity", "surfacing_validity", "served_true_effect")},
            "consumer_uplift_true": h.get("consumer_uplift_true", float("nan")),
            "credits_from_promotion": reasons.get("promotion", 0.0) /
            max(1e-9, sum(v for k, v in reasons.items() if k in ("promotion", "fetch"))),
            "solve_rate": s["solve_rate"],
            "x16_spearman_composite_task": spearman([r["composite"] for r in rows], [r["task_score"] for r in rows])
            if len(rows) > 2 else float("nan"),
            "x16_spearman_vacuous_cycles": _rho(vac_rows), "x16_spearman_real_cycles": _rho(real_rows),
            "x16_vacuous_cycles_publishable": mean_or_nan([r["composite"] >= PUBLISH_BAR for r in vac_rows]),
            "x16_vacuous_failed_task_publishable": mean_or_nan([r["composite"] >= PUBLISH_BAR for r in vac_rows
                                                                if not r["task_success"]]),
            "x16_n_vacuous_cycles": len(vac_rows), "x16_n_real_cycles": len(real_rows)}


def main():
    args = parse_args(__doc__.split("\n")[0], default_seeds=20)
    reduced = args.quick or live(args)
    arms = ["base"] if reduced else list(ARMS)
    rows = pmap(one, [(s, a, args) for a in arms for s in range(args.seeds)], args.workers)
    summ = summarize_groups(by_arm(rows, arms), METRICS)
    out = {"config": {"llm": args.llm, "seeds": args.seeds, "arms": arms, "mix": DEFAULT_MIX,
                      "population": "40 agents x 30 epochs" if not reduced else "reduced"},
           "raw": rows, "summary": summ}
    b = summ["base"]
    v = {
        "never_reused_gt_0.9": b["never_reused_published"]["mean"] > 0.9,
        "credits_concentrated_top10_gt_0.5": b["credit_top10_share"]["mean"] > 0.5,
        "credits_mostly_from_publishing": b["credits_from_promotion"]["mean"] > 0.5,
        "vacuous_share_promoted_gt_0.5": b["vacuous_share_promoted"]["mean"] > 0.5,
        "trivial_command_share_promoted_gt_0.84 (study notion)": b["trivial_command_share_promoted"]["mean"] > 0.84,
        "zero_reviews_ge_0.9": b["zero_review_share_promoted"]["mean"] >= 0.9,
        "gdi_intrinsic_dominates_variance (B9)": b["gdi_var_share_I"]["mean"] > 0.5,
        "gdi_largest_variance_component": max("IUSF", key=lambda k: b[f"gdi_var_share_{k}"]["mean"] or 0),
        "rank_weakly_related_abs_lt_0.3": abs(b["rank_validity"]["mean"]) < 0.3 if b["rank_validity"]["n"] else None,
        "x16_composite_not_task_gain_abs_rho_lt_0.3": abs(b["x16_spearman_composite_task"]["mean"]) < 0.3,
        "x16_vacuous_rho": b["x16_spearman_vacuous_cycles"]["mean"],
        "x16_real_rho": b["x16_spearman_real_cycles"]["mean"],
        "x16_failed_tasks_still_publishable_share": b["x16_vacuous_failed_task_publishable"]["mean"],
    }
    rates = {"base": FARM_RATE, "farm_rate_2": 2, "farm_rate_1": 1}
    if sum(a in summ for a in rates) > 1:      # the headline percentages as a function of the farm rate
        v["farm_rate_sensitivity"] = {rate: {m: summ[a][m]["mean"] for m in FARM_RATE_METRICS}
                                      for a, rate in rates.items() if a in summ}
    if "no_farmers" in summ:
        v["never_reused_without_farmers"] = summ["no_farmers"]["never_reused_published"]["mean"]
        v["vacuous_without_farmers"] = summ["no_farmers"]["vacuous_share_promoted"]["mean"]
        v["vacuous_with_real_validation_writers"] = summ["real_validation"]["vacuous_share_promoted"]["mean"]
        v["paired_never_reused_base_minus_nofarmers"] = paired(
            [r["never_reused_published"] for r in rows if r["arm"] == "no_farmers"],
            [r["never_reused_published"] for r in rows if r["arm"] == "base"])
    out["verdict"] = v
    save("x7_behind_evomap", out, args.out)
    print_groups(summ, METRICS, 34)
    print("verdict:", v)


if __name__ == "__main__":
    main()
