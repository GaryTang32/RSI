"""Retry round 2, L11: one multi-task GEPA search vs 30 single-task searches at equal total rollouts (RuleWorld).

Run: python experiments/gepa/r2_multitask.py [--seeds 20] [--workers 2]   (env R2_SEED0: first seed, default 200)
"""
from __future__ import annotations

import os

import numpy as np

from _common import by_seed, fmt_diff, paired, parse_args, pool_map, reflection_llm, save, summarize
from rsi.core.tasks import TaskSuite
from rsi.domains.ruleworld import make_domain
from rsi.domains.ruleworld.domain import RuleWorldDomain
from rsi.gepa import Config, run

BUDGETS = [10, 20, 50]
SEED0 = int(os.environ.get("R2_SEED0", 200))


def sub_domain(d, ids):
    dd = RuleWorldDomain(d.world, feedback=d.feedback)
    dd.tasks = TaskSuite([d.tasks.tasks[i] for i in d.tasks.tasks], {"evolve": list(ids)})
    return dd


def job(spec):
    seed, k = spec
    d = make_domain(seed=seed)
    T = list(d.tasks.splits["evolve"])
    seed_art = d.seed_artifact()
    per = lambda art: d.expected_per_task(art, "evolve")
    rm = run(sub_domain(d, T), seed_art, llm_propose=reflection_llm("sim", d.world),
             config=Config(max_metric_calls=k * len(T), seed=seed, val_split=None))
    single, single_heldout, single_calls = [], [], 0
    for j, t in enumerate(T):
        rs = run(sub_domain(d, [t]), seed_art, llm_propose=reflection_llm("sim", d.world),
                 config=Config(max_metric_calls=k, seed=seed * 1000 + j, val_split=None, minibatch_size=1))
        single.append(per(rs.best)[t])
        single_heldout.append(d.expected(rs.best, "test"))
        single_calls += rs.state.n_reflection_calls
    return {"seed": seed, "k": k, "seed_score": float(np.mean(list(per(seed_art).values()))),
            "multi": float(np.mean(list(per(rm.best).values()))), "single": float(np.mean(single)),
            "multi_heldout": d.expected(rm.best, "test"), "seed_heldout": d.expected(seed_art, "test"),
            "single_heldout_mean": float(np.mean(single_heldout)),
            "multi_reflection_calls": rm.state.n_reflection_calls, "single_reflection_calls": single_calls,
            "multi_rollouts": rm.state.counter.total}


def main():
    a = parse_args("retry-2 L11 multi-task inference-time search", default_seeds=20)
    seeds = list(range(SEED0, SEED0 + a.seeds))
    rows = pool_map(job, [(s, k) for k in BUDGETS for s in seeds], a.workers)
    A = {}
    for k in BUDGETS:
        rs = by_seed(rows, k=k)
        col = lambda key: [r[key] for r in rs]
        A[f"k={k}"] = {
            "multi": summarize(col("multi")), "single": summarize(col("single")), "seed": summarize(col("seed_score")),
            "multi_minus_single": paired(col("single"), col("multi")),
            "heldout_multi_minus_seed": paired(col("seed_heldout"), col("multi_heldout")),
            "heldout_multi_minus_single_prompts": paired(col("single_heldout_mean"), col("multi_heldout")),
            "reflection_calls_multi": summarize(col("multi_reflection_calls")),
            "reflection_calls_single_total": summarize(col("single_reflection_calls"))}
    t11a = all(A[f"k={k}"]["multi_minus_single"]["lo"] > 0 for k in (10, 20))
    t11b = A["k=10"]["heldout_multi_minus_seed"]["lo"] > 0
    A["T11a_multi_beats_single_at_k10_and_k20"] = bool(t11a)
    A["T11b_heldout_gain_at_k10"] = bool(t11b)
    v = "; ".join(f"k={k}: multi {A[f'k={k}']['multi']['mean']:.3f} vs single {A[f'k={k}']['single']['mean']:.3f}, "
                  f"diff {fmt_diff(A[f'k={k}']['multi_minus_single'])}" for k in BUDGETS)
    v += f". T11a={t11a}, T11b={t11b}"
    save("r2_multitask", {"experiment": "retry-2 L11 multi-task vs single-task", "config": {
        "seeds": [SEED0, SEED0 + a.seeds - 1], "budgets_per_task": BUDGETS, "n_tasks": 30},
        "analysis": A, "verdict": v, "raw": rows}, a.out)
    print(v)


if __name__ == "__main__":
    main()
