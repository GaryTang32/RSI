"""X6: sharing lets a lesson learned once be inherited by many agents, even on different models.
Run: python experiments/evomap/x6_sharing.py [--llm sim|claude:haiku] [--seeds N] [--quick]
"""
from __future__ import annotations

import random

from _common import (agent_config, by_arm, live, make_hub, make_world, mean_or_nan, paired, parse_args, pmap,
                     print_groups, save, save_figure, simulator, summarize_groups)

import numpy as np

from rsi.evomap import AgentSpec

ARMS = ("isolated", "safe_hub")
METRICS = ["solve_rate", "solve_rate_weak", "solve_rate_strong", "last10_solve_rate", "proposer_calls",
           "tokens_per_solve", "hub_gene_cycles", "weak_hub_genes_from_strong"]


def specs_for(seed: int, classes, n: int = 24) -> list[AgentSpec]:
    """Alternating strong (ability +0.8, insight 0.8) and weak (ability -1.0, insight 0.2) honest agents."""
    rng = random.Random(f"x6-{seed}")
    out = []
    for i in range(n):
        strong = i % 2 == 0
        out.append(AgentSpec(f"a{i:02d}_{'strong' if strong else 'weak'}", "honest",
                             ability=0.8 if strong else -1.0, insight=0.8 if strong else 0.2,
                             classes=rng.sample(classes, 4) if classes else None))
    return out


def one(job) -> dict:
    seed, arm, args = job
    world = make_world(args, seed)
    n, epochs = (4, 3) if live(args) else ((16, 10) if args.quick else (24, 30))
    specs = specs_for(seed, world.domain.tasks.families("evolve"), n)
    hub = make_hub("none" if arm == "isolated" else "safe", world, seed=seed)
    sim = simulator(world, hub, specs, agent_config("safe", seed), seed)
    s = sim.run(epochs)
    rows = [r for r in sim.cycles if "task_success" in r]
    weak = [r for r in rows if r["agent"].endswith("weak")]
    strong = [r for r in rows if r["agent"].endswith("strong")]
    inherited = []      # per weak hub-sourced cycle: was the gene written by a strong agent?
    for r in weak:
        if r["source"] == "hub" and r["gene_id"]:
            g = sim.agents[r["agent"]].store.genes.get(r["gene_id"])
            author = ((g.provenance or {}).get("author") or "") if g else ""
            inherited.append(author.endswith("strong"))
    return {"seed": seed, "arm": arm, "solve_rate": float(np.mean([r["task_success"] for r in rows])),
            "solve_rate_weak": float(np.mean([r["task_success"] for r in weak])),
            "solve_rate_strong": float(np.mean([r["task_success"] for r in strong])),
            "proposer_calls": s["proposer_calls"], "tokens_per_solve": s["tokens_per_solve"],
            "hub_gene_cycles": s["hub_gene_cycles"],
            "weak_hub_genes_from_strong": mean_or_nan(inherited),
            "last10_solve_rate": float(np.mean([r["task_success"] for r in rows if r["epoch"] >= epochs - 10])),
            "curve": [e["solve_rate"] for e in s["epochs"]], "solves": sum(r["task_success"] for r in rows)}


def draw_curves(by: dict):
    def draw(plt):
        fig, ax = plt.subplots(figsize=(5.5, 3.5))
        for a, c in zip(ARMS, ("#7f8c8d", "#2471a3")):
            cur = np.array([r["curve"] for r in by[a]])
            m = cur.mean(0)
            ax.plot(m, label=a, color=c)
            ax.fill_between(range(len(m)), np.percentile(cur, 10, 0), np.percentile(cur, 90, 0), color=c, alpha=0.15)
        ax.set_xlabel("epoch"), ax.set_ylabel("population solve rate"), ax.legend(frameon=False)
        return fig
    return draw


def main():
    args = parse_args(__doc__.split("\n")[0], default_seeds=20)
    rows = pmap(one, [(s, a, args) for a in ARMS for s in range(args.seeds)], args.workers)
    by = by_arm(rows, ARMS)
    summ = summarize_groups(by, METRICS)
    pd = {m: paired([r[m] for r in by["isolated"]], [r[m] for r in by["safe_hub"]]) for m in METRICS
          if m not in ("hub_gene_cycles", "weak_hub_genes_from_strong")}
    out = {"config": {"llm": args.llm, "seeds": args.seeds}, "raw": rows, "summary": summ,
           "paired_hub_minus_isolated": pd}
    out["verdict"] = {
        "hub_solves_more": pd["solve_rate"]["lo"] > 0,
        "fewer_redundant_discoveries": pd["proposer_calls"]["hi"] < 0,
        "fewer_tokens_per_solve": pd["tokens_per_solve"]["hi"] < 0,
        "weak_models_benefit": pd["solve_rate_weak"]["lo"] > 0,
        "weak_inherit_from_strong_share": summ["safe_hub"]["weak_hub_genes_from_strong"]["mean"],
    }
    save("x6_sharing", out, args.out)
    save_figure("x6_sharing", draw_curves(by))
    print_groups(summ, METRICS, 28)
    print("verdict:", out["verdict"])


if __name__ == "__main__":
    main()
