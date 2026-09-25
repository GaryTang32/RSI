"""X10: adoption-based ranking + adoption-only credits + verified execution fix the incentives.

Claim (spec §9.2 design, answering [abs:BE]): with SafeHub, reuse goes up, vacuous assets among promoted go
to ~0, rank validity (Spearman of the hub rank vs the true effect) becomes positive, farmers earn ~0, and
exploration slots cut the time to first reuse for new good assets.

Arms (same 40-agent population and seeds as X7): ``naive`` (faithful agents on NaiveEvoMapHub),
``safe`` (safe agents on SafeHub, exploration share eps=0.2), ``safe_no_explore`` (eps=0).

Run: python experiments/evomap/x10_safehub.py [--llm sim|claude:haiku] [--seeds N] [--quick]
"""
from __future__ import annotations

from _common import (DEFAULT_MIX, agent_config, figure_path, fmt, live, make_hub, make_world, paired, parse_args,
                     pmap, population_specs, save, summarize)

import numpy as np

from rsi.evomap import PopulationSimulator

ARMS = {"naive": ("naive", "naive", 0.0), "safe": ("safe", "safe", 0.2), "safe_no_explore": ("safe", "safe", 0.0)}


def one(job):
    seed, arm, args = job
    hub_kind, regime, eps = ARMS[arm]
    world = make_world(args, seed)
    n, epochs = (6, 3) if live(args) else ((24, 10) if args.quick else (40, 30))
    specs = population_specs(n, DEFAULT_MIX, seed, classes=world.domain.tasks.families("evolve"))
    hub = make_hub(hub_kind, world, eps=eps, seed=seed)
    sim = PopulationSimulator(world.domain, world.harness, hub, specs, config=agent_config(regime, seed),
                              model_factory=world.model_factory, proposer_factory=world.proposer_factory,
                              forge=world.forge, truth=world.truth, seed=seed)
    s = sim.run(epochs)
    h = s["hub"]
    recs = hub.published()
    ver = [r for r in recs if r.status in ("verified", "promoted", "deprecated")] if hub_kind == "safe" else \
        [r for r in recs if r.status == "promoted"]
    firsts, adopted = [], []
    for r in ver:
        eps_ = [a["epoch"] for a in r.adoptions if a["consumer"] != r.author and (a.get("counted") or hub_kind ==
                                                                                   "naive")]
        adopted.append(bool(eps_))
        if eps_:
            firsts.append(min(eps_) - r.epoch)
    per_epoch = [e["solve_rate"] for e in s["epochs"]]
    return {"seed": seed, "arm": arm, "reuse_rate_published": h["reuse_rate_published"],
            "reuse_rate_promoted": h["reuse_rate_promoted"], "never_reused_published": h["never_reused_published"],
            "vacuous_share_promoted": h["vacuous_share_promoted"], "rank_validity": h["rank_validity"], "surfacing_validity": h["surfacing_validity"],
            "served_true_effect": h["served_true_effect"],
            "farmer_credit_share": s["credit_share_by_kind"].get("farmer", 0.0),
            "honest_credit_share": s["credit_share_by_kind"].get("honest", 0.0) +
            s["credit_share_by_kind"].get("inflator", 0.0),
            "credit_gini": h["credit_gini"], "credit_top10_share": h["credit_top10_share"],
            "consumer_uplift_true": h.get("consumer_uplift_true", float("nan")),
            "hub_gene_true_uplift": s["hub_gene_true_uplift"], "solve_rate": s["solve_rate"],
            "n_published": h["n_published"], "n_promoted": h["n_promoted"],
            "verified_ever_adopted": float(np.mean(adopted)) if adopted else float("nan"),
            "time_to_first_reuse": float(np.median(firsts)) if firsts else float("nan"),
            "poisoned_in_stores": s["poisoned_in_stores"], "per_epoch_solve": per_epoch}


METRICS = ["reuse_rate_published", "reuse_rate_promoted", "never_reused_published", "vacuous_share_promoted",
           "rank_validity", "surfacing_validity", "served_true_effect", "farmer_credit_share", "honest_credit_share",
           "credit_gini", "credit_top10_share",
           "consumer_uplift_true", "hub_gene_true_uplift", "solve_rate", "n_published", "n_promoted",
           "verified_ever_adopted", "time_to_first_reuse", "poisoned_in_stores"]


def main():
    args = parse_args(__doc__.split("\n")[0], default_seeds=20)
    arms = list(ARMS)
    rows = pmap(one, [(s, a, args) for a in arms for s in range(args.seeds)], args.workers)
    by = {a: [r for r in rows if r["arm"] == a] for a in arms}
    out = {"config": {"llm": args.llm, "seeds": args.seeds, "mix": DEFAULT_MIX,
                      "population": "reduced" if (args.quick or live(args)) else "40 agents x 30 epochs"},
           "raw": rows, "summary": {a: {m: summarize([r[m] for r in by[a]]) for m in METRICS} for a in arms}}
    pd = {m: paired([r[m] for r in by["naive"]], [r[m] for r in by["safe"]]) for m in
          ("reuse_rate_promoted", "vacuous_share_promoted", "rank_validity", "surfacing_validity", "served_true_effect", "farmer_credit_share", "solve_rate",
           "consumer_uplift_true")}
    pe = {m: paired([r[m] for r in by["safe_no_explore"]], [r[m] for r in by["safe"]]) for m in
          ("time_to_first_reuse", "verified_ever_adopted", "solve_rate")}
    s, nv = out["summary"]["safe"], out["summary"]["naive"]
    out["paired_safe_minus_naive"] = pd
    out["paired_explore_minus_no_explore"] = pe
    out["verdict"] = {
        "reuse_up_promoted": pd["reuse_rate_promoted"]["lo"] > 0,
        "vacuous_near_zero": (s["vacuous_share_promoted"]["mean"] or 0) < 0.05,
        "rank_validity_positive": s["rank_validity"]["lo"] > 0 if s["rank_validity"]["n"] else None,
        "surfacing_validity_up": pd["surfacing_validity"]["lo"] > 0 if pd["surfacing_validity"]["n"] else None,
        "farmers_earn_near_zero": s["farmer_credit_share"]["mean"] < 0.01,
        "consumer_uplift_up": pd["consumer_uplift_true"]["lo"] > 0 if pd["consumer_uplift_true"]["n"] else None,
        "exploration_cuts_time_to_first_reuse": pe["time_to_first_reuse"]["hi"] < 0 if pe["time_to_first_reuse"]["n"]
        else None,
        "exploration_raises_adoption_of_verified": pe["verified_ever_adopted"]["lo"] > 0,
    }
    save("x10_safehub", out, args.out)
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(1, 2, figsize=(10, 3.6))
        for a, c in zip(arms, ("#c0392b", "#2471a3", "#7fb3d5")):
            curves = np.array([r["per_epoch_solve"] for r in by[a]])
            ax[0].plot(curves.mean(0), label=a, color=c)
        ax[0].set_xlabel("epoch"), ax[0].set_ylabel("population solve rate"), ax[0].legend(frameon=False)
        ms = ["reuse_rate_promoted", "vacuous_share_promoted", "surfacing_validity", "farmer_credit_share"]
        x = np.arange(len(ms))
        for i, (a, c) in enumerate(zip(arms, ("#c0392b", "#2471a3", "#7fb3d5"))):
            ax[1].bar(x + (i - 1) * 0.27, [out["summary"][a][m]["mean"] or 0 for m in ms], 0.27, label=a, color=c)
        ax[1].set_xticks(x, ["reuse\n(promoted)", "vacuous\n(promoted)", "surfacing\nvalidity", "farmer\ncredits"])
        ax[1].axhline(0, color="k", lw=0.5)
        fig.tight_layout()
        fig.savefig(figure_path("x10_safehub"), dpi=120)
    except Exception as e:  # noqa: BLE001
        print("figure skipped:", e)
    for a in arms:
        print(f"== {a}")
        for m in METRICS:
            print(f"  {m:28s} {fmt(out['summary'][a][m])}")
    print("verdict:", out["verdict"])


if __name__ == "__main__":
    main()
