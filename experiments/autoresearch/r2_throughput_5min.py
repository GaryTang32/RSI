"""R2-X2 - throughput at upstream's own 5-minute budget (claims P29, O2; retry round 2).

Claim [ar/program.md:114]: "If each experiment takes you ~5 minutes then you can run approx
12/hour, for a total of about 100 over the duration of the average human sleep."
[doc:42]: "~12 experiments an hour on a single GPU, with no human in the loop".

The first round measured throughput at 2-8 s budgets only. Here the budget is upstream's 300 s
(tinylm, CPU), so the question is the one the claim asks: does one experiment of the whole loop
(agent turn + commit + run + parse + keep/reset) take about the budget?

Arms (preregistered, claims-audit.md section 5, X2):
  scripted   the scripted agent: framework overhead only
  agent      live Claude Haiku 4.5 through AgentEditor (``claude -p`` with Read/Edit/Write tools,
             the editor closest to upstream's Claude Code), fresh cache dir
  rewrite    live Claude Haiku 4.5 through RewriteEditor (not preregistered as a run; the
             RewriteEditor rate is projected from latencies instead)

Per-experiment loop time = difference of successive decision timestamps (trajectory ``t``) for
experiments 1..N; experiments/hour = 3600 / mean. ``shadow_monitor`` and ``hidden_audit`` are off
(upstream has neither).

Usage: python experiments/autoresearch/r2_throughput_5min.py --arm scripted|agent [--budget 300] [--experiments 3]
"""
from __future__ import annotations

from _common import ROOT, write  # noqa: I001

import argparse
import json
import os
import time
from pathlib import Path

import numpy as np

from rsi.autoresearch import Config, run
from rsi.core import CachedLLM, ClaudeCLI
from rsi.domains.tinylm import TinyLMTask

SCRATCH = Path(os.environ.get("RSI_AR_SCRATCH", "/tmp/rsi_autoresearch_runs")) / "r2_throughput"


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--arm", choices=("scripted", "agent", "rewrite"), required=True)
    ap.add_argument("--budget", type=float, default=300.0)
    ap.add_argument("--experiments", type=int, default=3)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--max-usd", type=float, default=0.6)
    a = ap.parse_args()
    out_dir = SCRATCH / f"{a.arm}_{a.budget:g}s_{a.seed}"
    cache = SCRATCH / f"cache_{a.arm}_{a.budget:g}s_{a.seed}"
    task = TinyLMTask(budget_s=a.budget)
    llm = None
    if a.arm != "scripted":
        llm = CachedLLM(ClaudeCLI("haiku", timeout_s=900), cache)
    cfg = Config(max_experiments=a.experiments, seed=a.seed, shadow_monitor=False, hidden_audit=False, plot=False,
                 overwrite=True, max_usd=a.max_usd if llm is not None else None, tag=f"r2-tput-{a.arm}")
    t0 = time.time()
    res = run(task, llm_propose=llm, config=cfg, out_dir=out_dir, editor="agent" if a.arm == "agent" else "rewrite")
    wall = time.time() - t0
    traj = res.meta.get("trajectory") or json.loads((out_dir / "trajectory.json").read_text())
    decisions = [r for r in traj if r.get("status") is not None]
    ts = [r["t"] for r in decisions]
    per_exp = [ts[i] - ts[i - 1] for i in range(1, len(ts))]
    run_walls = [r.get("wall_s") for r in decisions]
    usage = res.usage or {}
    lat = []
    for d in sorted(cache.glob("*/*.json")) if llm is not None else []:
        try:
            lat.append(json.loads(d.read_text())["usage"]["latency_s"])
        except (KeyError, ValueError, OSError):
            pass
    mean_exp = float(np.mean(per_exp)) if per_exp else float("nan")
    out = {
        "config": {"arm": a.arm, "budget_s": a.budget, "experiments": a.experiments, "seed": a.seed,
                   "task": "tinylm", "mode": "hardened", "keep_rule": "upstream", "shadow_monitor": False,
                   "hidden_audit": False, "editor": {"agent": "AgentEditor", "rewrite": "RewriteEditor"}.get(a.arm),
                   "llm": None if llm is None else "claude-haiku-4-5 (claude -p)"},
        "decisions": [{k: r.get(k) for k in ("exp", "status", "metric", "description", "wall_s", "t")} for r in decisions],
        "per_experiment_loop_s": per_exp, "mean_loop_s": mean_exp,
        "experiments_per_hour": 3600.0 / mean_exp if per_exp else None,
        "run_wall_s": run_walls, "llm_latency_s": lat, "total_wall_s": wall,
        "usage": usage.get("_total") if isinstance(usage, dict) else str(usage),
        "results_tsv": (out_dir / "results.tsv").read_text(),
    }
    rate = out["experiments_per_hour"]
    out["verdict"] = {"experiments_per_hour": rate, "within_pm15pct_of_12": bool(rate is not None and 10.2 <= rate <= 13.8),
                      "projected_per_8h_night": None if rate is None else 8 * rate}
    write(f"r2_throughput_{a.arm}_{a.budget:g}s", out)
    print(json.dumps(out["verdict"], indent=1), per_exp)


if __name__ == "__main__":
    main()
