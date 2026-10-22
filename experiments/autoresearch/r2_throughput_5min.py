"""R2-X2 - throughput at upstream's own 5-minute budget (claims P29, O2; retry round 2).

Usage: python experiments/autoresearch/r2_throughput_5min.py --arm scripted|agent [--budget 300] [--experiments 3]
"""
from _common import SCRATCH, write  # noqa: I001

import argparse
import json
import time

import numpy as np

from rsi.autoresearch import Config, run
from rsi.core import CachedLLM, ClaudeCLI
from rsi.domains.tinylm import TinyLMTask

OUT = SCRATCH / "r2_throughput"


def cache_latencies(cache) -> list[float]:
    lat = []
    for d in sorted(cache.glob("*/*.json")):
        try:
            lat.append(json.loads(d.read_text())["usage"]["latency_s"])
        except (KeyError, ValueError, OSError):
            pass
    return lat


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--arm", choices=("scripted", "agent", "rewrite"), required=True)
    ap.add_argument("--budget", type=float, default=300.0)
    ap.add_argument("--experiments", type=int, default=3)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--max-usd", type=float, default=0.6)
    a = ap.parse_args()
    out_dir = OUT / f"{a.arm}_{a.budget:g}s_{a.seed}"
    cache = OUT / f"cache_{a.arm}_{a.budget:g}s_{a.seed}"
    task = TinyLMTask(budget_s=a.budget)
    llm = None if a.arm == "scripted" else CachedLLM(ClaudeCLI("haiku", timeout_s=900), cache)
    # shadow_monitor and hidden_audit are off: upstream has neither
    cfg = Config(max_experiments=a.experiments, seed=a.seed, shadow_monitor=False, hidden_audit=False, plot=False,
                 overwrite=True, max_usd=a.max_usd if llm is not None else None, tag=f"r2-tput-{a.arm}")
    t0 = time.time()
    res = run(task, llm_propose=llm, config=cfg, out_dir=out_dir, editor="agent" if a.arm == "agent" else "rewrite")
    wall = time.time() - t0
    traj = res.meta.get("trajectory") or json.loads((out_dir / "trajectory.json").read_text())
    decisions = [r for r in traj if r.get("status") is not None]
    ts = [r["t"] for r in decisions]
    per_exp = [ts[i] - ts[i - 1] for i in range(1, len(ts))]  # whole-loop time per experiment
    usage = res.usage or {}
    mean_exp = float(np.mean(per_exp)) if per_exp else float("nan")
    out = {
        "config": {"arm": a.arm, "budget_s": a.budget, "experiments": a.experiments, "seed": a.seed,
                   "task": "tinylm", "mode": "hardened", "keep_rule": "upstream", "shadow_monitor": False,
                   "hidden_audit": False, "editor": {"agent": "AgentEditor", "rewrite": "RewriteEditor"}.get(a.arm),
                   "llm": None if llm is None else "claude-haiku-4-5 (claude -p)"},
        "decisions": [{k: r.get(k) for k in ("exp", "status", "metric", "description", "wall_s", "t")}
                      for r in decisions],
        "per_experiment_loop_s": per_exp, "mean_loop_s": mean_exp,
        "experiments_per_hour": 3600.0 / mean_exp if per_exp else None,
        "run_wall_s": [r.get("wall_s") for r in decisions],
        "llm_latency_s": cache_latencies(cache) if llm is not None else [], "total_wall_s": wall,
        "usage": usage.get("_total") if isinstance(usage, dict) else str(usage),
        "results_tsv": (out_dir / "results.tsv").read_text(),
    }
    rate = out["experiments_per_hour"]
    out["verdict"] = {"experiments_per_hour": rate,
                      "within_pm15pct_of_12": bool(rate is not None and 10.2 <= rate <= 13.8),
                      "projected_per_8h_night": None if rate is None else 8 * rate}
    write(f"r2_throughput_{a.arm}_{a.budget:g}s", out)
    print(json.dumps(out["verdict"], indent=1), per_exp)


if __name__ == "__main__":
    main()
