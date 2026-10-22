"""R2-X3d - does a slow machine's winner stack carry over to a fast machine's baseline? (claim O17, retry round 2).

Usage: python experiments/autoresearch/r2_machine_transfer.py --only 0,1,2 (resumable, claim files) | --analyze
"""
from _common import SCRATCH, cached_json, claim, constants, write  # noqa: I001

import argparse
import json
import math

import numpy as np

from rsi.autoresearch import AutoresearchLoop, Config, MockResearchAgent
from rsi.core.artifact import Artifact
from rsi.domains.tinylm import TinyLMTask

SLOW_S, FAST_S = 2.0, 8.0  # a "machine" is a wall-clock budget on the same CPU
N_EXP = 30
SEEDS = tuple(range(6))
RUN_SEEDS = tuple(30_000 + i for i in range(5))
OUT = SCRATCH / "r2_machine_transfer"


def _night(seed: int, budget_s: float, tag: str) -> dict:
    task = TinyLMTask(budget_s=budget_s)
    cfg = Config(max_experiments=N_EXP, seed=seed, shadow_monitor=False, hidden_audit=False, plot=False,
                 overwrite=True, tag=f"r2-xfer-{tag}-{seed}")
    res = AutoresearchLoop(task, MockResearchAgent(task.mock_edit_pool(), seed=seed), cfg,
                           out_dir=OUT / f"{tag}_{seed}").run()
    a = res.meta["analysis"]
    return {"budget_s": budget_s, "baseline": a["baseline"], "best": a["best"], "n_keep": a["n_keep"],
            "keeps": [d["description"] for d in a["top_hits"]], "final_train_py": res.best["train.py"]}


def _score(train_py: str, budget_s: float) -> list:
    """One hardened run per run seed; None for a crashed or diverged run."""
    task = TinyLMTask(budget_s=budget_s)
    art = Artifact({**task.seed_artifact().files, "train.py": train_py})
    vals = [task.run(art, seed=rs, mode="hardened").metric for rs in RUN_SEEDS]
    return [m if m is not None and math.isfinite(m) else None for m in vals]


def zero_gain_mean(vals: list, base_mean: float) -> float:
    """Mean with every crashed/diverged run (None/NaN) scored as the baseline mean (zero gain)."""
    return float(np.mean([v if v is not None and v == v else base_mean for v in vals]))


def _pair(seed: int) -> dict:
    slow = _night(seed, SLOW_S, "slow")
    fast = _night(seed, FAST_S, "fast")
    base_train = TinyLMTask(budget_s=FAST_S).seed_artifact().files["train.py"]
    trains = (("base", base_train), ("slow_final", slow.pop("final_train_py")),
              ("fast_final", fast.pop("final_train_py")))
    return {"seed": seed, "slow": {**slow, "final_constants": constants(trains[1][1])},
            "fast": {**fast, "final_constants": constants(trains[2][1])},
            "on_fast": {k: _score(t, FAST_S) for k, t in trains}, "on_slow": {k: _score(t, SLOW_S) for k, t in trains},
            "base_constants": constants(base_train)}


def pair(seed: int) -> dict:
    """Both nights of an agent seed, then every final scored back to back on both machines (cached)."""
    return cached_json(OUT / f"pair_{seed}.json", lambda: _pair(seed))


def per_seed(p: dict) -> dict:
    f, s = p["on_fast"], p["on_slow"]
    bf = float(np.mean([v for v in f["base"] if v is not None]))
    sf, ff = zero_gain_mean(f["slow_final"], bf), zero_gain_mean(f["fast_final"], bf)
    bs = float(np.mean([v for v in s["base"] if v is not None]))
    fs, ss = zero_gain_mean(s["fast_final"], bs), zero_gain_mean(s["slow_final"], bs)
    return {"seed": p["seed"], "D": sf - ff, "rho": (bf - sf) / (bf - ff) if bf != ff else float("nan"),
            "gain_transferred": bf - sf, "gain_own": bf - ff,
            "failed_runs_transferred": sum(v is None for v in f["slow_final"]),
            "failed_runs_own": sum(v is None for v in f["fast_final"]),
            "transferred_loses_to_base": bool(sf > bf),
            "reverse": {"D": fs - ss, "rho": (bs - fs) / (bs - ss) if bs != ss else float("nan"),
                        "failed_runs": sum(v is None for v in s["fast_final"])}}


def tstats(x) -> dict:
    from scipy import stats

    x = np.asarray(x, float)
    n, m, sd = len(x), float(x.mean()), float(x.std(ddof=1))
    se = sd / math.sqrt(n)
    h = float(stats.t.ppf(0.975, n - 1)) * se
    t = m / se if se > 0 else float("inf")
    mde = (stats.t.ppf(0.95, n - 1) + stats.t.ppf(0.80, n - 1)) * se
    return {"n": n, "mean": m, "sd": sd, "t_ci95": [m - h, m + h], "p_one_sided": float(stats.t.sf(t, n - 1)),
            "mde_80pct_power": float(mde)}


def analyze(pairs: list[dict]) -> dict:
    """Primary: D = mean8s(slow_final) - mean8s(fast_final) > 0, one-sided paired t over the seeds."""
    rows = [per_seed(p) for p in pairs]
    prim = tstats([r["D"] for r in rows])
    return {"per_seed": rows, "primary_D": {**prim, "pass": bool(prim["mean"] > 0 and prim["p_one_sided"] < 0.05)},
            "rho": tstats([r["rho"] for r in rows]),
            "reverse_D": tstats([r["reverse"]["D"] for r in rows]),
            "failed_runs_transferred": sum(r["failed_runs_transferred"] for r in rows),
            "failed_runs_own": sum(r["failed_runs_own"] for r in rows),
            "seeds_transferred_loses_to_base": sum(r["transferred_loses_to_base"] for r in rows)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--only", type=str, default="")
    ap.add_argument("--analyze", action="store_true")
    a = ap.parse_args()
    if a.only:
        OUT.mkdir(parents=True, exist_ok=True)
        for s in [int(x) for x in a.only.split(",")]:
            if claim(OUT / f"claim_{s}"):
                pair(s)
                print(f"seed {s} done", flush=True)
    if a.analyze:
        pairs = [pair(s) for s in SEEDS]
        verdict = analyze(pairs)
        write("r2_machine_transfer", {"config": {"slow_budget_s": SLOW_S, "fast_budget_s": FAST_S,
                                                 "experiments_per_night": N_EXP, "agent_seeds": list(SEEDS),
                                                 "run_seeds": list(RUN_SEEDS), "agent": "MockResearchAgent greedy",
                                                 "keep_rule": "upstream", "mode": "hardened"},
                                      "pairs": pairs, "verdict": verdict})
        print(json.dumps({k: v for k, v in verdict.items() if k != "per_seed"}, indent=1, default=str))
        for r in verdict["per_seed"]:
            print(r)


if __name__ == "__main__":
    main()
