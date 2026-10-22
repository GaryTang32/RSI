"""X10: adoption-based ranking + adoption-only credits + verified execution fix the incentives.
Run: python experiments/evomap/x10_safehub.py [--llm sim|claude:haiku] [--seeds N] [--quick]
"""
from __future__ import annotations

from _common import (DEFAULT_MIX, agent_config, by_arm, live, make_hub, make_world, mean_or_nan, paired, parse_args,
                     pmap, population_specs, print_groups, save, save_figure, simulator, summarize_groups)

import numpy as np

ARMS = {"naive": ("naive", "naive", 0.0), "safe": ("safe", "safe", 0.2), "safe_no_explore": ("safe", "safe", 0.0)}
COLORS = ("#c0392b", "#2471a3", "#7fb3d5")
HUB_KEYS = ("reuse_rate_published", "reuse_rate_promoted", "never_reused_published", "vacuous_share_promoted",
            "rank_validity", "surfacing_validity", "served_true_effect")
METRICS = ["reuse_rate_published", "reuse_rate_promoted", "never_reused_published", "vacuous_share_promoted",
           "rank_validity", "surfacing_validity", "served_true_effect", "farmer_credit_share", "honest_credit_share",
           "credit_gini", "credit_top10_share",
           "consumer_uplift_true", "hub_gene_true_uplift", "solve_rate", "n_published", "n_promoted", "n_admitted",
           "trivial_command_share_promoted", "verified_ever_adopted", "time_to_first_reuse", "poisoned_in_stores",
           "poisoned_in_stores_hub", "poisoned_in_stores_self"]


def one(job) -> dict:
    seed, arm, args = job
    hub_kind, regime, eps = ARMS[arm]
    world = make_world(args, seed)
    n, epochs = (6, 3) if live(args) else ((24, 10) if args.quick else (40, 30))
    specs = population_specs(n, DEFAULT_MIX, seed, classes=world.domain.tasks.families("evolve"))
    hub = make_hub(hub_kind, world, eps=eps, seed=seed)
    s = simulator(world, hub, specs, agent_config(regime, seed), seed).run(epochs)
    h = s["hub"]
    served = {"verified", "promoted", "deprecated"} if hub_kind == "safe" else {"promoted"}
    firsts, adopted = [], []
    for r in hub.published():
        if r.status not in served:
            continue
        reuse = [a["epoch"] for a in r.adoptions
                 if a["consumer"] != r.author and (a.get("counted") or hub_kind == "naive")]
        adopted.append(bool(reuse))
        if reuse:
            firsts.append(min(reuse) - r.epoch)
    kind_share = s["credit_share_by_kind"]
    return {"seed": seed, "arm": arm, **{k: h[k] for k in HUB_KEYS},
            "farmer_credit_share": kind_share.get("farmer", 0.0),
            "honest_credit_share": kind_share.get("honest", 0.0) + kind_share.get("inflator", 0.0),
            "credit_gini": h["credit_gini"], "credit_top10_share": h["credit_top10_share"],
            "consumer_uplift_true": h.get("consumer_uplift_true", float("nan")),
            "hub_gene_true_uplift": s["hub_gene_true_uplift"], "solve_rate": s["solve_rate"],
            "n_published": h["n_published"], "n_promoted": h["n_promoted"], "n_admitted": h["n_admitted"],
            "trivial_command_share_promoted": h["trivial_command_share_promoted"],
            "verified_ever_adopted": mean_or_nan(adopted),
            "time_to_first_reuse": float(np.median(firsts)) if firsts else float("nan"),
            "poisoned_in_stores": s["poisoned_in_stores"],
            # harmful cards taken from the hub vs harmful cards an honest agent's own writer produced
            "poisoned_in_stores_hub": s["poisoned_in_stores_hub"],
            "poisoned_in_stores_self": s["poisoned_in_stores_self"],
            "per_epoch_solve": [e["solve_rate"] for e in s["epochs"]]}


def draw_panels(by: dict, summ: dict):
    def draw(plt):
        fig, ax = plt.subplots(1, 2, figsize=(10, 3.6))
        for a, c in zip(ARMS, COLORS):
            ax[0].plot(np.array([r["per_epoch_solve"] for r in by[a]]).mean(0), label=a, color=c)
        ax[0].set_xlabel("epoch"), ax[0].set_ylabel("population solve rate"), ax[0].legend(frameon=False)
        ms = ["reuse_rate_promoted", "vacuous_share_promoted", "surfacing_validity", "farmer_credit_share"]
        x = np.arange(len(ms))
        for i, (a, c) in enumerate(zip(ARMS, COLORS)):
            ax[1].bar(x + (i - 1) * 0.27, [summ[a][m]["mean"] or 0 for m in ms], 0.27, label=a, color=c)
        ax[1].set_xticks(x, ["reuse\n(promoted)", "vacuous\n(promoted)", "surfacing\nvalidity", "farmer\ncredits"])
        ax[1].axhline(0, color="k", lw=0.5)
        return fig
    return draw


def main():
    args = parse_args(__doc__.split("\n")[0], default_seeds=20)
    rows = pmap(one, [(s, a, args) for a in ARMS for s in range(args.seeds)], args.workers)
    by = by_arm(rows, ARMS)
    summ = summarize_groups(by, METRICS)
    out = {"config": {"llm": args.llm, "seeds": args.seeds, "mix": DEFAULT_MIX,
                      "population": "reduced" if (args.quick or live(args)) else "40 agents x 30 epochs"},
           "raw": rows, "summary": summ}

    def gain(base, arm, metrics):
        return {m: paired([r[m] for r in by[base]], [r[m] for r in by[arm]]) for m in metrics}
    pd = gain("naive", "safe", ("reuse_rate_promoted", "vacuous_share_promoted", "rank_validity", "surfacing_validity",
                                "served_true_effect", "farmer_credit_share", "solve_rate", "consumer_uplift_true"))
    pe = gain("safe_no_explore", "safe", ("time_to_first_reuse", "verified_ever_adopted", "solve_rate"))
    s = summ["safe"]
    out["paired_safe_minus_naive"] = pd
    out["paired_explore_minus_no_explore"] = pe
    out["verdict"] = {
        "reuse_up_promoted": pd["reuse_rate_promoted"]["lo"] > 0,
        "vacuous_near_zero": (s["vacuous_share_promoted"]["mean"] or 0) < 0.05,
        "rank_validity_positive": s["rank_validity"]["lo"] > 0 if s["rank_validity"]["n"] else None,
        "rank_validity_ci": [s["rank_validity"]["lo"], s["rank_validity"]["hi"]],
        "rank_validity_better_than_naive": pd["rank_validity"]["lo"] > 0 if pd["rank_validity"]["n"] else None,
        "no_hub_poison_in_safe_stores": s["poisoned_in_stores_hub"]["mean"] == 0,
        "surfacing_validity_up": pd["surfacing_validity"]["lo"] > 0 if pd["surfacing_validity"]["n"] else None,
        "farmers_earn_near_zero": s["farmer_credit_share"]["mean"] < 0.01,
        "consumer_uplift_up": pd["consumer_uplift_true"]["lo"] > 0 if pd["consumer_uplift_true"]["n"] else None,
        "exploration_cuts_time_to_first_reuse": pe["time_to_first_reuse"]["hi"] < 0 if pe["time_to_first_reuse"]["n"]
        else None,
        "exploration_raises_adoption_of_verified": pe["verified_ever_adopted"]["lo"] > 0,
    }
    save("x10_safehub", out, args.out)
    save_figure("x10_safehub", draw_panels(by, summ))
    print_groups(summ, METRICS, 28)
    print("verdict:", out["verdict"])


if __name__ == "__main__":
    main()
