"""E18: should the developer revise the latest version (paper §3) or the strongest one (Listing 2)? Exploratory.

    python experiments/dream-rsi/e18_developer_base.py [--seeds 20] [--workers 2]
"""
import numpy as np
from _common import domain_of, paired, parse_args, pmap, save, summ

from rsi.dream import Config, ParametricMutator, run

ST = dict(grid=(6, 4), W=6, rounds=8, M=6)  # E3's synthetic setting
BUDGET = ST["rounds"] * ST["grid"][0] * (ST["grid"][1] + 1)


def one(job):
    seed, base = job
    cfg = Config(rounds=40, W=ST["W"], branch_count=ST["grid"][0], refine_count=ST["grid"][1], M=ST["M"],
                 sandbox="inprocess", seed=seed, max_calls=BUDGET, agent_workers=1, trace=False, developer_base=base)
    res = run(domain_of("synthetic", seed), config=cfg, developer=ParametricMutator())
    sel = [r["dream"]["selected"] for r in res.trajectory if "dream" in r]
    return {"seed": seed, "base": base, "final_best": res.meta["best_score"], "rounds": len(res.trajectory),
            "calls": res.usage["_cost"]["agent_calls"],
            "selected_new_version_share": float(np.mean([s > 0 for s in sel])) if sel else None}


def main():
    a = parse_args("E18 developer base", default_seeds=20)
    rows = pmap(one, [(s, b) for s in range(a.seeds) for b in ("strongest", "latest")], a.workers)
    by = {b: {r["seed"]: r for r in rows if r["base"] == b} for b in ("strongest", "latest")}
    seeds = sorted(by["strongest"])
    res = {"final_best": {b: summ([by[b][s]["final_best"] for s in seeds]) for b in by},
           "latest_minus_strongest": paired([by["strongest"][s]["final_best"] for s in seeds],
                                            [by["latest"][s]["final_best"] for s in seeds]),
           "selected_new_version_share": {b: summ([by[b][s]["selected_new_version_share"] for s in seeds]) for b in by},
           "runs_completed": len(rows), "runs_expected": 2 * a.seeds}
    print(res)
    save("e18_developer_base", {"config": {**ST, "budget": BUDGET, "seeds": a.seeds, "developer": "ParametricMutator"},
                                "results": res, "raw": rows})


if __name__ == "__main__":
    main()
