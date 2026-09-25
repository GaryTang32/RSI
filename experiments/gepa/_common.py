"""Shared plumbing for the GEPA experiments (E1-E13).

* ``parse_args``: ``--llm sim|claude:<model>``, ``--seeds N``, ``--quick``, ``--workers``, ``--out``;
* ``reflection_llm``: the reflection LM - RuleWorld / AgentQA mocks offline, or a cached
  headless ``claude -p`` backend (cache under ``.rsi_cache/gepa``) for live showcases;
* ``pool_map``: runs independent (seed, arm) jobs in worker processes (serial for live LLMs);
* ``summarize`` / ``paired`` / ``save`` / ``plot_curves``: mean + 95% bootstrap CI via
  ``rsi.core.stats`` and JSON / PNG outputs under ``results/gepa``.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from multiprocessing import get_context
from pathlib import Path
from typing import Callable, Optional, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from rsi.core import CachedLLM, ClaudeCLI, paired_diff_ci, summarize_runs  # noqa: E402

RESULTS = ROOT / "results" / "gepa"
CACHE = ROOT / ".rsi_cache" / "gepa"


def parse_args(desc: str, default_seeds: int = 30, extra: Optional[Callable] = None):
    ap = argparse.ArgumentParser(description=desc)
    ap.add_argument("--llm", default="sim", help="reflection LM: sim (offline mock) | claude:<model> (live, cached)")
    ap.add_argument("--seeds", type=int, default=None, help=f"number of seeds (default {default_seeds}; quick: 4)")
    ap.add_argument("--quick", action="store_true", help="few seeds / small budgets for a fast check")
    ap.add_argument("--workers", type=int, default=3, help="parallel processes (runs)")
    ap.add_argument("--out", default=None, help="output JSON path")
    if extra:
        extra(ap)
    a = ap.parse_args()
    a.live = a.llm != "sim"
    if a.seeds is None:
        a.seeds = 1 if a.live else (4 if a.quick else default_seeds)
    if a.live:
        a.workers = 1
        print("[note] live reflection LM: small budgets, results cached under .rsi_cache/gepa", flush=True)
    return a


def reflection_llm(spec: str, world=None, kind: str = "ruleworld", profile=None):
    """The reflection LM for one run."""
    if spec == "sim":
        if kind == "ruleworld":
            from rsi.domains.ruleworld import RuleWorldReflectionLM
            return RuleWorldReflectionLM(world, profile)
        if kind == "agentqa":
            from rsi.gepa import AgentQAReflectionLM
            return AgentQAReflectionLM()
        from rsi.gepa import GenericReflectionLM
        return GenericReflectionLM()
    k, _, model = spec.partition(":")
    if k != "claude":
        raise SystemExit(f"unsupported --llm {spec!r}")
    CACHE.mkdir(parents=True, exist_ok=True)
    return CachedLLM(ClaudeCLI(model or "haiku", timeout_s=240), CACHE)


def pool_map(fn: Callable, jobs: Sequence, workers: int = 3) -> list:
    if workers <= 1 or len(jobs) <= 1:
        return [fn(j) for j in jobs]
    ctx = get_context("fork")
    with ctx.Pool(workers) as p:
        return p.map(fn, jobs, chunksize=1)


def clean(xs) -> list[float]:
    return [float(x) for x in xs if x is not None and not (isinstance(x, float) and math.isnan(x))]


def summarize(xs) -> dict:
    v = clean(xs)
    return summarize_runs(v) if v else {"mean": float("nan"), "lo": float("nan"), "hi": float("nan"), "n": 0}


def paired(a, b) -> dict:
    """Paired difference b - a over seeds."""
    pairs = [(x, y) for x, y in zip(a, b) if x is not None and y is not None]
    if not pairs:
        return {}
    return paired_diff_ci([p[0] for p in pairs], [p[1] for p in pairs])


def fmt(s: dict) -> str:
    return f"{s['mean']:.3f} [{s['lo']:.3f}, {s['hi']:.3f}]"


def save(name: str, payload: dict, out: Optional[str] = None) -> Path:
    RESULTS.mkdir(parents=True, exist_ok=True)
    p = Path(out) if out else RESULTS / f"{name}.json"
    payload.setdefault("generated_at", time.strftime("%Y-%m-%d %H:%M:%S"))
    p.write_text(json.dumps(payload, indent=1, default=_default))
    print(f"[saved] {p}")
    return p


def _default(x):
    if isinstance(x, (np.floating, np.integer)):
        return x.item()
    if isinstance(x, np.ndarray):
        return x.tolist()
    if isinstance(x, (set, tuple)):
        return list(x)
    return repr(x)


def plot_curves(path: Path, budgets: Sequence[int], arms: dict[str, list[list[float]]], title: str,
                ylabel: str = "true test score of returned candidate", hline: Optional[float] = None,
                logx: bool = True) -> None:
    """Mean +- 95% bootstrap CI band of per-seed curves (seeds x budgets)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(7, 4.2))
    for name, curves in arms.items():
        m = np.asarray(curves, float)
        mean, lo, hi = [], [], []
        for j in range(m.shape[1]):
            s = summarize(m[:, j])
            mean.append(s["mean"]), lo.append(s["lo"]), hi.append(s["hi"])
        ax.plot(budgets, mean, marker="o", ms=3, label=name)
        ax.fill_between(budgets, lo, hi, alpha=0.18)
    if hline is not None:
        ax.axhline(hline, color="k", ls="--", lw=0.8, label="target")
    if logx:
        ax.set_xscale("log")
    ax.set_xlabel("rollouts (metric calls)")
    ax.set_ylabel(ylabel)
    ax.set_title(title, fontsize=10)
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)
    print(f"[saved] {path}")


def bar_plot(path: Path, labels: Sequence[str], stats: Sequence[dict], title: str, ylabel: str) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(max(5, 1.1 * len(labels)), 3.8))
    means = [s["mean"] for s in stats]
    err = [[s["mean"] - s["lo"] for s in stats], [s["hi"] - s["mean"] for s in stats]]
    ax.bar(range(len(labels)), means, yerr=err, capsize=3, color="#4c72b0")
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=25, ha="right", fontsize=8)
    ax.set_ylabel(ylabel)
    ax.set_title(title, fontsize=10)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)
    print(f"[saved] {path}")
