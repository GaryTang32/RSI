"""X11: untrusted assets must be quarantined and re-tested before use.
Run: python experiments/evomap/x11_quarantine.py [--llm sim|claude:haiku] [--seeds N] [--quick]
"""
from __future__ import annotations

from _common import (agent_config, by_arm, live, make_hub, make_world, paired, parse_args, pmap, population_specs,
                     print_groups, save, simulator, summarize_groups)

import numpy as np

MIX = {"honest": 0.5, "freerider": 0.2, "poisoner": 0.2, "inflator": 0.1}
ARMS = {"isolated": ("none", None), "naive_direct": ("naive", "direct"), "naive_quarantine": ("naive", "quarantine"),
        "safe_direct": ("safe", "direct"), "safe_quarantine": ("safe", "quarantine")}
SIM_KEYS = ("poisoned_in_stores", "poisoned_in_stores_hub", "poisoned_in_stores_self")
METRICS = ["consumer_solve_rate", "poisoned_cycles", "poisoned_cycle_share", "poisoned_in_stores",
           "poisoned_in_stores_hub", "poisoned_in_stores_self", "hub_gene_cycles", "hub_gene_true_uplift",
           "quarantined", "quarantine_rejected", "poisoners_promoted"]


def one(job) -> dict:
    seed, arm, args = job
    hub_kind, reuse = ARMS[arm]
    world = make_world(args, seed)
    n, epochs = (6, 3) if live(args) else ((20, 8) if args.quick else (30, 20))
    specs = population_specs(n, MIX, seed, poison_rate=3, classes=world.domain.tasks.families("evolve"))
    hub = make_hub(hub_kind, world, seed=seed)
    cfg = agent_config("safe", seed, **({"reuse_mode": reuse} if reuse else {}))
    sim = simulator(world, hub, specs, cfg, seed)
    s = sim.run(epochs)
    rows = [r for r in sim.cycles if "task_success" in r and r["kind"] in ("honest", "freerider")]
    poisoned = [bool(r.get("gene_poisoned")) for r in rows]
    return {"seed": seed, "arm": arm, "consumer_solve_rate": float(np.mean([r["task_success"] for r in rows])),
            "poisoned_cycles": int(sum(poisoned)), "poisoned_cycle_share": float(np.mean(poisoned)),
            **{k: s[k] for k in SIM_KEYS}, "hub_gene_cycles": sum(r["source"] == "hub" for r in rows),
            "hub_gene_true_uplift": s["hub_gene_true_uplift"], "quarantined": s["quarantined"],
            "quarantine_rejected": s["quarantine_rejected"],
            "poisoners_promoted": sum(r.author.endswith("poisoner") and r.status in ("promoted", "verified")
                                      for r in (hub.published() if hub else []))}


def main():
    args = parse_args(__doc__.split("\n")[0], default_seeds=12)
    rows = pmap(one, [(s, a, args) for a in ARMS for s in range(args.seeds)], args.workers)
    by = by_arm(rows, ARMS)
    summ = summarize_groups(by, METRICS)

    def solve(a):
        return [r["consumer_solve_rate"] for r in by[a]]
    reg = {a: paired(solve("isolated"), solve(a)) for a in ARMS if a != "isolated"}
    q_vs_d = {h: paired(solve(f"{h}_direct"), solve(f"{h}_quarantine")) for h in ("naive", "safe")}
    out = {"config": {"llm": args.llm, "seeds": args.seeds, "mix": MIX}, "raw": rows, "summary": summ,
           "paired_vs_isolated": reg, "paired_quarantine_minus_direct": q_vs_d}
    out["verdict"] = {
        "quarantine_blocks_poison_naive": summ["naive_quarantine"]["poisoned_cycles"]["mean"] <=
        0.1 * max(1e-9, summ["naive_direct"]["poisoned_cycles"]["mean"]),
        "direct_apply_harm_measurable_naive": summ["naive_direct"]["poisoned_cycles"]["lo"] > 0,
        "no_regression_with_quarantine": all(reg[a]["lo"] > -0.05 for a in ("naive_quarantine", "safe_quarantine")),
        "safehub_alone_blocks_poison": summ["safe_direct"]["poisoners_promoted"]["mean"] == 0,
    }
    save("x11_quarantine", out, args.out)
    print_groups(summ, METRICS, 24)
    print("paired vs isolated:", {a: (round(v['mean_diff'], 3), round(v['lo'], 3), round(v['hi'], 3))
                                  for a, v in reg.items()})
    print("verdict:", out["verdict"])


if __name__ == "__main__":
    main()
