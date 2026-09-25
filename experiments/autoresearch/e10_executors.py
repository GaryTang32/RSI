"""E10 - scheduler/cluster forks: asynchronous submission and polling.

Claims [doc/spec]: the research-cluster fork runs the loop through a job scheduler
(inside a container); SkyPilot's parallel variant reports ~9x throughput and that
parallelism turned greedy hill-climbing into grid-like exploration.

A. Script-mode landscape (every experiment is a real `python train.py` subprocess that
   "trains" for 1.2 s), 36 experiments per night, 10% crash edits (OOM, NaN, hang, typo):
     sequential | LocalProcessExecutor(3 workers) | FakeSlurmExecutor(3 nodes, 0.2 s queue
     delay, container image) - the parallel arms use the "don't wait" loop.
   Measured: experiments/hour, time to reach a common best, SLURM terminal states per
   crash kind (OUT_OF_MEMORY / FAILED / TIMEOUT), share of experiments judged against a
   newer incumbent than they were proposed from (grid-likeness), final true quality.
B. tinylm (CPU-bound numpy training, 1.5 s budget): sequential vs 3 workers on a 4-core
   machine shared with other jobs.

With --llm claude:haiku (or the offline --llm scripted) part A runs with an LLM research
agent proposing every experiment (proposals are made while earlier runs are in flight);
part B is skipped.

Usage: python experiments/autoresearch/e10_executors.py [--seeds N] [--quick] [--llm sim|claude:haiku]
"""
from __future__ import annotations

from _common import suffix, SCRATCH, ci, is_live, parser, plt, research_agent, usage_of, write  # noqa: I001

import json
import time

import numpy as np

from rsi.autoresearch import AutoresearchLoop, Config, LandscapeTask
from rsi.autoresearch.executors import FakeSlurmExecutor, LocalProcessExecutor
from rsi.autoresearch.parallel import ParallelAutoresearchLoop

SLEEP_SCALE = 0.004          # 300 nominal seconds -> 1.2 s real per run


def one(arm: str, seed: int, n: int, task=None, crash_rate: float = 0.1, workers: int = 3,
        llm_spec: str = "sim") -> dict:
    task = task or LandscapeTask(inprocess=False, sleep_scale=SLEEP_SCALE)
    agent, llms = research_agent(llm_spec, task.mock_edit_pool(), seed=seed, crash_rate=crash_rate)
    cfg = Config(max_experiments=n, hidden_audit=False, plot=False, seed=seed, overwrite=True, tag=f"e10-{arm}-{seed}")
    out_dir = SCRATCH / "e10" / f"{task.name}_{arm}_{seed}"
    t0 = time.time()
    if arm == "sequential":
        loop = AutoresearchLoop(task, agent, cfg, out_dir=out_dir, llms=llms)
        pool = None
    else:
        cfg.workers = workers
        pool = LocalProcessExecutor(workers) if arm.startswith("local") else \
            FakeSlurmExecutor(nodes=workers, spool_dir=out_dir / "slurm", queue_delay_s=0.2,
                              container_image="nvcr.io/nvidia/pytorch:25.01", poll_s=0.05)
        loop = ParallelAutoresearchLoop(task, agent, cfg, out_dir=out_dir, pool=pool, llms=llms)
    res = loop.run()
    wall = time.time() - t0
    traj = res.trajectory
    t_base = traj[0]["t"]
    best_t, best = [], None
    for r in traj:
        if r["status"] == "keep" and r["metric"] is not None and (best is None or r["metric"] < best):
            best = r["metric"]
        best_t.append((r["t"] - t_base, best))
    nodes = [nd for nd in res.ledger.nodes() if nd.kind == "candidate" and nd.status not in ("invalid", "duplicate")]
    stale = [nd.meta.get("submitted_on") not in (None, "") and nd.meta.get("submitted_on") !=
             res.ledger[nd.parent].meta.get("commit") if nd.parent else False for nd in nodes]
    states = {}
    for nd in nodes:
        if nd.status == "crash":
            states.setdefault(nd.meta.get("crash_kind"), set()).add(nd.meta.get("slurm_state", "n/a"))
    truth = task.truth(res.best) if hasattr(task, "truth") else None
    return {"arm": arm, "seed": seed, "wall_s": wall, "loop_wall_s": res.meta["wall_s"],
            "experiments_per_hour": res.meta["analysis"].get("experiments_per_hour"),
            "best": res.meta["analysis"]["best"], "baseline": res.meta["analysis"]["baseline"],
            "best_over_time": best_t, "stale_fraction": float(np.mean(stale)) if stale else 0.0,
            "crash_states": {k: sorted(v) for k, v in states.items()}, "true_final": truth,
            "n_keep": res.meta["analysis"]["n_keep"], "n_crash": res.meta["analysis"]["n_crash"],
            "usage": usage_of(llms)}


def time_to(best_over_time, target) -> float:
    for t, b in best_over_time:
        if b is not None and b <= target + 1e-12:
            return t
    return float("nan")


def main():
    ap = parser(__doc__.splitlines()[0], seeds=3)
    a = ap.parse_args()
    live = is_live(a.llm)
    n = (6 if a.quick else 9) if live else (12 if a.quick else 36)
    seeds = list(range(a.seeds if live else 1 if a.quick else a.seeds))
    arms = ("sequential", "local-3", "slurm-3")
    runs = [one(arm, s, n, llm_spec=a.llm) for s in seeds for arm in arms]
    out = {"config": {"n_experiments": n, "seeds": seeds, "sleep_per_run_s": 300 * SLEEP_SCALE, "workers": 3,
                      "crash_rate": 0.1, "llm": a.llm}, "landscape": {}}
    for s in seeds:
        rs = [r for r in runs if r["seed"] == s]
        target = max(r["best"] for r in rs)          # a best that every arm reached
        for r in rs:
            r["time_to_common_best_s"] = time_to(r["best_over_time"], target)
    for arm in arms:
        rs = [r for r in runs if r["arm"] == arm]
        out["landscape"][arm] = {
            "experiments_per_hour": ci([r["experiments_per_hour"] for r in rs]),
            "loop_wall_s": ci([r["loop_wall_s"] for r in rs]),
            "time_to_common_best_s": ci([r["time_to_common_best_s"] for r in rs]),
            "stale_fraction": ci([r["stale_fraction"] for r in rs]),
            "true_final": ci([r["true_final"] for r in rs]),
            "crash_states": {k: sorted({x for r in rs for x in r["crash_states"].get(k, [])})
                             for k in sorted({k for r in rs for k in r["crash_states"]})},
            "runs": rs}
    # B: CPU-bound tinylm
    from rsi.domains.tinylm import TinyLMTask

    tl = [] if live else [one(arm, 0, 6 if a.quick else 12, task=TinyLMTask(budget_s=1.5), crash_rate=0.0)
                          for arm in ("sequential", "local-3")]
    out["tinylm"] = {r["arm"]: {k: r[k] for k in ("experiments_per_hour", "loop_wall_s", "best", "baseline")} for r in tl}
    L = out["landscape"]
    speedup = L["local-3"]["experiments_per_hour"]["mean"] / L["sequential"]["experiments_per_hour"]["mean"]
    speedup_slurm = L["slurm-3"]["experiments_per_hour"]["mean"] / L["sequential"]["experiments_per_hour"]["mean"]
    tl_speed = (out["tinylm"]["local-3"]["experiments_per_hour"] / out["tinylm"]["sequential"]["experiments_per_hour"]
                if tl else None)
    verdict = {"throughput_speedup_local3": speedup, "throughput_speedup_slurm3": speedup_slurm,
               "tinylm_speedup_local3_cpu_bound": tl_speed,
               "time_to_common_best_s": {arm: L[arm]["time_to_common_best_s"]["mean"] for arm in arms},
               "stale_fraction": {arm: L[arm]["stale_fraction"]["mean"] for arm in arms},
               "slurm_crash_states": L["slurm-3"]["crash_states"],
               "true_final": {arm: L[arm]["true_final"]["mean"] for arm in arms}}
    verdict["claim_reproduced"] = bool(speedup > 2.0 and speedup_slurm > 1.8 and
                                       L["local-3"]["time_to_common_best_s"]["mean"] <
                                       L["sequential"]["time_to_common_best_s"]["mean"])
    if live:
        verdict["usage"] = [r["usage"] for r in runs]
    out["verdict"] = verdict
    name = "e10_executors" + suffix(a.llm, a.quick)
    out["figure"] = str(figure(out, arms, name))
    write(name, out)
    print(json.dumps(verdict, indent=1, default=str))


def figure(out, arms, name):
    from _common import RESULTS

    p = plt()
    fig, ax = p.subplots(figsize=(8, 4.5))
    col = {"sequential": "#5f6368", "local-3": "#1e8e3e", "slurm-3": "#1a73e8"}
    for arm in arms:
        for r in out["landscape"][arm]["runs"]:
            ts = [t for t, b in r["best_over_time"]]
            bs = [b for t, b in r["best_over_time"]]
            ax.step(ts, bs, where="post", color=col[arm], alpha=0.7, label=arm if r["seed"] == 0 else None)
    ax.set_xlabel("wall-clock seconds")
    ax.set_ylabel("running best val_loss")
    ax.set_title("same experiment count: sequential vs 3 local workers vs fake SLURM (3 nodes)")
    ax.legend(fontsize=8)
    fig.tight_layout()
    path = RESULTS / f"{name}.png"
    RESULTS.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=110)
    return path


if __name__ == "__main__":
    main()
