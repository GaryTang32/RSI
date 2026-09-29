"""Retry round 2 - L11: do GEPA's lessons carry across tasks in inference-time search?

Paper App. E: passing the set of tasks as D_train = D_pareto "allows GEPA to apply lessons
and insights extracted from rollouts for one task to other tasks". If lessons carry, one
multi-task search should beat independent per-task searches at the same total rollouts
when the per-task budget is small, and its single prompt should also lift tasks it never
saw. (The claim audit ran only 50 rollouts per task, where each single-task run already
gets about 30x more reflection calls per task.)

RuleWorld default world, the 30 D_train tasks as "the tasks to solve", seeds 200-219,
per-task budgets k in {10, 20, 50}:

* ``multi``  - one GEPA run, D_train = D_pareto = the 30 tasks, B = 30 k, b = 3;
* ``single`` - 30 independent GEPA runs, one per task (D_train = D_pareto = {task}, b = 1,
  B = k each), the per-task search the paper's Sequential-refinement baselines stand for;
* held-out: the multi-task prompt and each single-task prompt on the 300 unseen test tasks.

Metric: mean exact expected score over the 30 tasks (analytic). Thresholds: claim audit,
retry round 2.

    python experiments/gepa/r2_multitask.py [--seeds 20] [--workers 2]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import paired, parse_args, pool_map, reflection_llm, save, summarize  # noqa: E402

import numpy as np  # noqa: E402

from rsi.core.tasks import TaskSuite  # noqa: E402
from rsi.domains.ruleworld import make_domain  # noqa: E402
from rsi.domains.ruleworld.domain import RuleWorldDomain  # noqa: E402
from rsi.gepa import Config, run  # noqa: E402

BUDGETS = [10, 20, 50]
SEED0 = int(__import__("os").environ.get("R2_SEED0", 200))


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
        rs = sorted([r for r in rows if r["k"] == k], key=lambda r: r["seed"])
        A[f"k={k}"] = {
            "multi": summarize([r["multi"] for r in rs]), "single": summarize([r["single"] for r in rs]),
            "seed": summarize([r["seed_score"] for r in rs]),
            "multi_minus_single": paired([r["single"] for r in rs], [r["multi"] for r in rs]),
            "heldout_multi_minus_seed": paired([r["seed_heldout"] for r in rs], [r["multi_heldout"] for r in rs]),
            "heldout_multi_minus_single_prompts": paired([r["single_heldout_mean"] for r in rs],
                                                         [r["multi_heldout"] for r in rs]),
            "reflection_calls_multi": summarize([r["multi_reflection_calls"] for r in rs]),
            "reflection_calls_single_total": summarize([r["single_reflection_calls"] for r in rs])}
    t11a = all(A[f"k={k}"]["multi_minus_single"]["lo"] > 0 for k in (10, 20))
    t11b = A["k=10"]["heldout_multi_minus_seed"]["lo"] > 0
    A["T11a_multi_beats_single_at_k10_and_k20"] = bool(t11a)
    A["T11b_heldout_gain_at_k10"] = bool(t11b)
    v = "; ".join(f"k={k}: multi {A[f'k={k}']['multi']['mean']:.3f} vs single {A[f'k={k}']['single']['mean']:.3f}, "
                  f"diff {A[f'k={k}']['multi_minus_single']['mean_diff']:+.3f} [{A[f'k={k}']['multi_minus_single']['lo']:+.3f}, "
                  f"{A[f'k={k}']['multi_minus_single']['hi']:+.3f}]" for k in BUDGETS)
    v += f". T11a={t11a}, T11b={t11b}"
    save("r2_multitask", {"experiment": "retry-2 L11 multi-task vs single-task", "config": {
        "seeds": [SEED0, SEED0 + a.seeds - 1], "budgets_per_task": BUDGETS, "n_tasks": 30},
        "analysis": A, "verdict": v, "raw": rows}, a.out)
    print(v)


if __name__ == "__main__":
    main()
