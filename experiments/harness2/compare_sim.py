"""Offline comparison on AgentQA with the simulated model: seed vs Harness² (k=1, k=3) vs budget-matched baselines.

Every arm sees the same task stream (AgentQA holdout + ood: 43 tasks, 5 families) in the same shuffled order per
seed, with the same simulated task model; arms differ only in what they do with their agent calls (rollouts):

- ``seed``            H0, one rollout per task (the rollout Harness² labels ``base1``)
- ``harness2_k1``     Harness², parallel, k = 1: 4 rollouts per task (1 reference, 2 probes, 1 composed re-run)
- ``harness2_k3``     Harness², parallel, k = 3: 12 rollouts per task
- ``harness2_k1_nolib``  the paper's "w/o library" ablation at k = 1 (4 rollouts per task)
- ``best_of_4`` / ``best_of_12``  H0 rolled 4 / 12 times + the SAME anonymised selector (rollout-matched to k = 1 / 3)
- ``gepa_matched``    GEPA trained on the labelled evolve split with 4N - N rollouts (N = stream size), then one
                      rollout per stream task: equal agent calls to Harness² k = 1, but it USES LABELS on a separate
                      practice split (numeric only), so it is a different information regime, not a like-for-like
                      test-time method

Accuracy is graded against the evaluation-only labels after every loop has ended. CIs: percentile bootstrap over
tasks (per-task accuracy averaged over seeds first, so a task is the resampling unit), paired by task for diffs.

Run: python experiments/harness2/compare_sim.py [--seeds 3] [--quick] [--out results/harness2/compare_sim.json]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from rsi.core.stats import bootstrap_ci, paired_diff_ci  # noqa: E402
from rsi.domains.agentqa import AgentQADomain, SimModel, make_suite  # noqa: E402
from rsi.harness2 import Config, MockImprover, best_of_n, open_task, run  # noqa: E402
from rsi.harness2.api import stream_tasks  # noqa: E402
from rsi.harness2.loop import _seed  # noqa: E402

RESULTS = ROOT / "results" / "harness2"
STREAM = "holdout+ood"


def arm_seed(dom, tasks, s):
    sim = SimModel(dom.tasks)
    out = {}
    for t in tasks:
        ex = dom.rollout(dom.seed_artifact(), open_task(t), seed=_seed(s, t.id, "base1"), llm=sim)
        out[t.id] = float(dom.grade_execution(t, ex)[0])
    return out, {"rollouts": len(tasks), "task_llm_calls": sim.meter.total().calls, "improver_calls": 0}


def arm_harness2(dom, tasks, s, k, library=True):
    sim, imp = SimModel(dom.tasks), MockImprover()
    res = run(dom, dom.seed_artifact(), llm_task=sim, llm_propose=imp,
              config=Config(k=k, mode="parallel", seed=s, library=library, report=True, trace=False), tasks=tasks)
    recs = res.meta["records"]
    scores = {tid: v["submitted"] for tid, v in res.meta["report"]["per_task"].items()}
    extra = {"rollouts": sum(len(r.rollouts) for r in recs), "task_llm_calls": sim.meter.total().calls,
             "improver_calls": imp.meter.total().calls, "library_entries": res.meta["library_entries"],
             "kept_base_tasks": sum(r.kept_base() for r in recs),
             "fell_back_stages": sum(r.counts()["fell_back"] for r in recs)}
    return scores, extra


def arm_best_of(dom, tasks, s, n):
    sim, sel = SimModel(dom.tasks), MockImprover()
    out = best_of_n(dom, dom.seed_artifact(), llm_task=sim, llm_select=sel, n=n, tasks=tasks, seed=s)
    scores = {tid: v["submitted"] for tid, v in out["report"]["per_task"].items()}
    return scores, {"rollouts": sum(len(r.rollouts) for r in out["records"]),
                    "task_llm_calls": sim.meter.total().calls, "improver_calls": sel.meter.total().calls}


def arm_gepa(dom, tasks, s, train_calls):
    from rsi.gepa import AgentQAReflectionLM
    from rsi.gepa import Config as GConfig
    from rsi.gepa import run as grun
    sim = SimModel(dom.tasks)
    res = grun(dom, dom.seed_artifact(), llm_task=sim, llm_propose=AgentQAReflectionLM(),
               config=GConfig(max_metric_calls=train_calls, seed=s, trace=False, shadow_monitor=False,
                              val_split=None))
    calls_train = sim.meter.total().calls
    out = {}
    for t in tasks:
        ex = dom.rollout(res.best, open_task(t), seed=_seed(s, t.id, "base1"), llm=sim)
        out[t.id] = float(dom.grade_execution(t, ex)[0])
    return out, {"rollouts": calls_train + len(tasks), "task_llm_calls": sim.meter.total().calls,
                 "improver_calls": None, "train_rollouts": calls_train,
                 "best_artifact_diff": dom.seed_artifact().diff(res.best)[:2000]}


def summarize(per_seed: list[dict[str, float]], ref: list[dict[str, float]] | None, families: dict) -> dict:
    ids = sorted(per_seed[0])
    acc = np.array([[ps[t] for t in ids] for ps in per_seed])         # seeds x tasks
    by_task = acc.mean(axis=0)
    point, lo, hi = bootstrap_ci(by_task, seed=0)
    out = {"accuracy": point, "ci95": [lo, hi], "per_seed": [float(x) for x in acc.mean(axis=1)],
           "families": {f: float(np.mean([by_task[i] for i, t in enumerate(ids) if families[t] == f]))
                        for f in sorted(set(families.values()))}}
    if ref is not None:
        ref_by_task = np.array([[ps[t] for t in ids] for ps in ref]).mean(axis=0)
        d = paired_diff_ci(ref_by_task, by_task, seed=0)
        out["vs_seed"] = {"diff": d["mean_diff"], "ci95": [d["lo"], d["hi"]], "n_tasks": d["n"]}
    return out


def paired(a: list[dict], b: list[dict]) -> dict:
    ids = sorted(a[0])
    x = np.array([[ps[t] for t in ids] for ps in a]).mean(axis=0)
    y = np.array([[ps[t] for t in ids] for ps in b]).mean(axis=0)
    d = paired_diff_ci(x, y, seed=0)
    return {"diff": d["mean_diff"], "ci95": [d["lo"], d["hi"]], "n_tasks": d["n"]}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--seeds", type=int, default=3)
    ap.add_argument("--quick", action="store_true", help="1 seed, 12 tasks")
    ap.add_argument("--out", default=str(RESULTS / "compare_sim.json"))
    a = ap.parse_args()
    seeds = 1 if a.quick else a.seeds
    suite = make_suite(seed=0)
    dom = AgentQADomain(suite)
    t0 = time.time()
    arms = ["seed", "harness2_k1", "harness2_k3", "harness2_k1_nolib", "best_of_4", "best_of_12", "gepa_matched"]
    scores: dict[str, list[dict]] = {k: [] for k in arms}
    costs: dict[str, list[dict]] = {k: [] for k in arms}
    streams = []
    for s in range(seeds):
        tasks = stream_tasks(dom, Config(split=STREAM, order="shuffle", seed=s))
        if a.quick:
            tasks = tasks[:12]
        streams.append([t.id for t in tasks])
        n = len(tasks)
        jobs = {"seed": lambda: arm_seed(dom, tasks, s),
                "harness2_k1": lambda: arm_harness2(dom, tasks, s, 1),
                "harness2_k3": lambda: arm_harness2(dom, tasks, s, 3),
                "harness2_k1_nolib": lambda: arm_harness2(dom, tasks, s, 1, library=False),
                "best_of_4": lambda: arm_best_of(dom, tasks, s, 4),
                "best_of_12": lambda: arm_best_of(dom, tasks, s, 12),
                "gepa_matched": lambda: arm_gepa(dom, tasks, s, 4 * n - n)}
        for name in arms:
            t1 = time.time()
            sc, extra = jobs[name]()
            scores[name].append(sc)
            costs[name].append({**extra, "rollouts_per_task": extra["rollouts"] / n, "wall_s": time.time() - t1})
            print(f"seed {s} {name:18s} acc={np.mean(list(sc.values())):.3f} "
                  f"rollouts/task={extra['rollouts'] / n:.1f}", flush=True)
    families = {t: suite.get(t).family for t in streams[0]}
    summary = {name: {**summarize(scores[name], None if name == "seed" else scores["seed"], families),
                      "rollouts_per_task": float(np.mean([c["rollouts_per_task"] for c in costs[name]])),
                      "costs": costs[name]} for name in arms}
    pairs = {"harness2_k1 - best_of_4": paired(scores["best_of_4"], scores["harness2_k1"]),
             "harness2_k3 - best_of_12": paired(scores["best_of_12"], scores["harness2_k3"]),
             "harness2_k3 - harness2_k1": paired(scores["harness2_k1"], scores["harness2_k3"]),
             "harness2_k1 - harness2_k1_nolib": paired(scores["harness2_k1_nolib"], scores["harness2_k1"]),
             "harness2_k1 - gepa_matched": paired(scores["gepa_matched"], scores["harness2_k1"])}
    out = {"experiment": "harness2/compare_sim", "domain": "agentqa (make_suite(seed=0))", "stream": STREAM,
           "n_tasks": len(streams[0]), "seeds": seeds, "order": "shuffle per seed (Random(f'{s}:__domains__'), "
           "Random(f'{s}:{domain}'))", "task_model": "SimModel (offline)",
           "improver": "rsi.harness2.MockImprover (offline, deterministic)",
           "ci": "95% percentile bootstrap over tasks (per-task accuracy averaged over seeds), paired by task",
           "labels": "evaluation only: every arm is graded after its loop; Harness² and Best-of-n never read a "
                     "label; GEPA trains on the labelled evolve split",
           "arms": summary, "paired": pairs, "streams": streams,
           "per_task": {name: scores[name] for name in arms}, "wall_s": time.time() - t0}
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(out, indent=1, default=float))
    print("\narm                 acc    95% CI           vs seed (paired)          rollouts/task")
    for name in arms:
        r = summary[name]
        vs = r.get("vs_seed")
        vs_txt = f"{vs['diff']:+.3f} [{vs['ci95'][0]:+.3f}, {vs['ci95'][1]:+.3f}]" if vs else "-"
        print(f"{name:18s} {r['accuracy']:.3f}  [{r['ci95'][0]:.3f}, {r['ci95'][1]:.3f}]  {vs_txt:26s} "
              f"{r['rollouts_per_task']:.1f}")
    for k, v in pairs.items():
        print(f"{k:34s} {v['diff']:+.3f} [{v['ci95'][0]:+.3f}, {v['ci95'][1]:+.3f}]")
    print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
