"""E18 - Which version does the developer revise? §3 (pi^m -> pi^(m+1)) vs Listing 2 ("start from a strong
recent policy") (claims audit M12, retry round 2).

The paper's two texts disagree: §3 p.6 has the developer revise pi^m into pi^(m+1) (the unpublished method draft
agrees: pi^(j) -> F^(j) -> pi^(j+1)), while Listing 2 L2:247 tells the developer to "start from a strong recent
policy". ``Config.developer_base`` now implements both. This exploratory run (no hypothesis) compares them on
E3's synthetic setting at equal agent-call budget, same seeds.

    python experiments/dream-rsi/e18_developer_base.py [--seeds 20] [--workers 2]
"""
import numpy as np

from _common import domain_of, paired, parse_args, pmap, save, summ

from rsi.dream import Config, ParametricMutator, run

ST = dict(grid=(6, 4), W=6, rounds=8, M=6)


def one(job):
    seed, base = job
    dom = domain_of("synthetic", seed)
    per = ST["grid"][0] * (ST["grid"][1] + 1)
    cfg = Config(rounds=40, W=ST["W"], branch_count=ST["grid"][0], refine_count=ST["grid"][1], M=ST["M"],
                 sandbox="inprocess", seed=seed, max_calls=ST["rounds"] * per, agent_workers=1, trace=False,
                 developer_base=base)
    res = run(dom, config=cfg, developer=ParametricMutator())
    sel = [r["dream"]["selected"] for r in res.trajectory if "dream" in r]
    return {"seed": seed, "base": base, "final_best": res.meta["best_score"], "rounds": len(res.trajectory),
            "calls": res.usage["_cost"]["agent_calls"],
            "selected_new_version_share": float(np.mean([s > 0 for s in sel])) if sel else None}


def main():
    a = parse_args("E18 developer base", default_seeds=20)
    rows = pmap(one, [(s, b) for s in range(a.seeds) for b in ("strongest", "latest")], a.workers)
    by = {b: {r["seed"]: r for r in rows if r["base"] == b} for b in ("strongest", "latest")}
    seeds = sorted(by["strongest"])
    x = [by["strongest"][s]["final_best"] for s in seeds]
    y = [by["latest"][s]["final_best"] for s in seeds]
    res = {"final_best": {b: summ([by[b][s]["final_best"] for s in seeds]) for b in by},
           "latest_minus_strongest": paired(x, y),
           "selected_new_version_share": {b: summ([by[b][s]["selected_new_version_share"] for s in seeds]) for b in by},
           "runs_completed": len(rows), "runs_expected": 2 * a.seeds}
    print(res)
    save("e18_developer_base", {"config": {**ST, "budget": ST["rounds"] * ST["grid"][0] * (ST["grid"][1] + 1),
                                           "seeds": a.seeds, "developer": "ParametricMutator"},
                                "results": res, "raw": rows})


if __name__ == "__main__":
    main()
