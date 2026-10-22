"""E10 - scheduler/cluster forks: asynchronous submission and polling.

Usage: python experiments/autoresearch/e10_executors.py [--seeds N] [--quick] [--llm sim|claude:haiku]
"""
from _common import (SCRATCH, ci, is_live, parser, plt, report, research_agent, running_best, save_fig,  # noqa: I001
                     suffix, time_to, usage_of)

import time

import numpy as np

from rsi.autoresearch import AutoresearchLoop, Config, LandscapeTask
from rsi.autoresearch.executors import FakeSlurmExecutor, LocalProcessExecutor
from rsi.autoresearch.parallel import ParallelAutoresearchLoop
from rsi.domains.tinylm import TinyLMTask

SLEEP_SCALE = 0.004  # 300 nominal seconds -> 1.2 s real per run
ARMS = ("sequential", "local-3", "slurm-3")


def one(arm: str, seed: int, n: int, task=None, crash_rate: float = 0.1, workers: int = 3,
        llm_spec: str = "sim") -> dict:
    task = task or LandscapeTask(inprocess=False, sleep_scale=SLEEP_SCALE)
    agent, llms = research_agent(llm_spec, task.mock_edit_pool(), seed=seed, crash_rate=crash_rate)
    cfg = Config(max_experiments=n, hidden_audit=False, plot=False, seed=seed, overwrite=True, tag=f"e10-{arm}-{seed}")
    out_dir = SCRATCH / "e10" / f"{task.name}_{arm}_{seed}"
    t0 = time.time()
    if arm == "sequential":
        loop = AutoresearchLoop(task, agent, cfg, out_dir=out_dir, llms=llms)
    else:
        cfg.workers = workers
        pool = LocalProcessExecutor(workers) if arm.startswith("local") else \
            FakeSlurmExecutor(nodes=workers, spool_dir=out_dir / "slurm", queue_delay_s=0.2,
                              container_image="nvcr.io/nvidia/pytorch:25.01", poll_s=0.05)
        loop = ParallelAutoresearchLoop(task, agent, cfg, out_dir=out_dir, pool=pool, llms=llms)
    res = loop.run()
    wall = time.time() - t0
    t_base = res.trajectory[0]["t"]
    best_t = [(r["t"] - t_base, b) for r, b in zip(res.trajectory, running_best(res.trajectory))]
    nodes = [nd for nd in res.ledger.nodes() if nd.kind == "candidate" and nd.status not in ("invalid", "duplicate")]
    # judged against a newer incumbent than the one it was proposed from (grid-likeness)
    stale = [bool(nd.parent) and nd.meta.get("submitted_on") not in (None, "")
             and nd.meta.get("submitted_on") != res.ledger[nd.parent].meta.get("commit") for nd in nodes]
    states = {}
    for nd in nodes:
        if nd.status == "crash":
            states.setdefault(nd.meta.get("crash_kind"), set()).add(nd.meta.get("slurm_state", "n/a"))
    a = res.meta["analysis"]
    return {"arm": arm, "seed": seed, "wall_s": wall, "loop_wall_s": res.meta["wall_s"],
            "experiments_per_hour": a.get("experiments_per_hour"), "best": a["best"], "baseline": a["baseline"],
            "best_over_time": best_t, "stale_fraction": float(np.mean(stale)) if stale else 0.0,
            "crash_states": {k: sorted(v) for k, v in states.items()},
            "true_final": task.truth(res.best) if hasattr(task, "truth") else None,
            "n_keep": a["n_keep"], "n_crash": a["n_crash"], "usage": usage_of(llms)}


def main() -> None:
    a = parser(__doc__.splitlines()[0], seeds=3).parse_args()
    live = is_live(a.llm)
    n = (6 if a.quick else 9) if live else (12 if a.quick else 36)
    seeds = list(range(a.seeds if live else 1 if a.quick else a.seeds))
    runs = [one(arm, s, n, llm_spec=a.llm) for s in seeds for arm in ARMS]
    out = {"config": {"n_experiments": n, "seeds": seeds, "sleep_per_run_s": 300 * SLEEP_SCALE, "workers": 3,
                      "crash_rate": 0.1, "llm": a.llm}, "landscape": {}}
    for s in seeds:
        rs = [r for r in runs if r["seed"] == s]
        target = max(r["best"] for r in rs)  # a best that every arm reached
        for r in rs:
            r["time_to_common_best_s"] = time_to(r["best_over_time"], target)
    for arm in ARMS:
        rs = [r for r in runs if r["arm"] == arm]
        out["landscape"][arm] = {
            **{k: ci([r[k] for r in rs]) for k in ("experiments_per_hour", "loop_wall_s", "time_to_common_best_s",
                                                    "stale_fraction", "true_final")},
            "crash_states": {k: sorted({x for r in rs for x in r["crash_states"].get(k, [])})
                             for k in sorted({k for r in rs for k in r["crash_states"]})},
            "runs": rs}
    # CPU-bound tinylm: sequential vs 3 workers
    tl = [] if live else [one(arm, 0, 6 if a.quick else 12, task=TinyLMTask(budget_s=1.5), crash_rate=0.0)
                          for arm in ("sequential", "local-3")]
    out["tinylm"] = {r["arm"]: {k: r[k] for k in ("experiments_per_hour", "loop_wall_s", "best", "baseline")}
                     for r in tl}
    L, T = out["landscape"], out["tinylm"]

    def speedup(arm: str) -> float:
        return L[arm]["experiments_per_hour"]["mean"] / L["sequential"]["experiments_per_hour"]["mean"]

    verdict = {"throughput_speedup_local3": speedup("local-3"), "throughput_speedup_slurm3": speedup("slurm-3"),
               "tinylm_speedup_local3_cpu_bound": (T["local-3"]["experiments_per_hour"] /
                                                   T["sequential"]["experiments_per_hour"] if tl else None),
               "time_to_common_best_s": {arm: L[arm]["time_to_common_best_s"]["mean"] for arm in ARMS},
               "stale_fraction": {arm: L[arm]["stale_fraction"]["mean"] for arm in ARMS},
               "slurm_crash_states": L["slurm-3"]["crash_states"],
               "true_final": {arm: L[arm]["true_final"]["mean"] for arm in ARMS}}
    verdict["claim_reproduced"] = bool(speedup("local-3") > 2.0 and speedup("slurm-3") > 1.8 and
                                       L["local-3"]["time_to_common_best_s"]["mean"] <
                                       L["sequential"]["time_to_common_best_s"]["mean"])
    if live:
        verdict["usage"] = [r["usage"] for r in runs]
    out["verdict"] = verdict
    name = "e10_executors" + suffix(a.llm, a.quick)
    out["figure"] = str(figure(out, name))
    report(name, out, default=str)


def figure(out: dict, name: str):
    p = plt()
    fig, ax = p.subplots(figsize=(8, 4.5))
    col = {"sequential": "#5f6368", "local-3": "#1e8e3e", "slurm-3": "#1a73e8"}
    for arm in ARMS:
        for r in out["landscape"][arm]["runs"]:
            ax.step([t for t, _ in r["best_over_time"]], [b for _, b in r["best_over_time"]], where="post",
                    color=col[arm], alpha=0.7, label=arm if r["seed"] == 0 else None)
    ax.set_xlabel("wall-clock seconds")
    ax.set_ylabel("running best val_loss")
    ax.set_title("same experiment count: sequential vs 3 local workers vs fake SLURM (3 nodes)")
    ax.legend(fontsize=8)
    return save_fig(fig, name)


if __name__ == "__main__":
    main()
