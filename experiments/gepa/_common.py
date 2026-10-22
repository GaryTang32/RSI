"""Shared plumbing for the GEPA experiments: CLI, reflection LM, worker pool, CI summaries, JSON and plots."""
from __future__ import annotations

import argparse
import json
import math
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


def parse_args(desc: str, default_seeds: int = 30, extra: Optional[Callable] = None) -> argparse.Namespace:
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
    """The offline mock for ``kind`` when ``spec == "sim"``, else a cached ``claude -p`` (``claude:<model>``)."""
    if spec == "sim":
        if kind == "ruleworld":
            from rsi.domains.ruleworld import RuleWorldReflectionLM
            return RuleWorldReflectionLM(world, profile)
        from rsi.gepa import AgentQAReflectionLM, GenericReflectionLM
        return AgentQAReflectionLM() if kind == "agentqa" else GenericReflectionLM()
    k, _, model = spec.partition(":")
    if k != "claude":
        raise SystemExit(f"unsupported --llm {spec!r}")
    CACHE.mkdir(parents=True, exist_ok=True)
    return CachedLLM(ClaudeCLI(model or "haiku", timeout_s=240), CACHE)


def pool_map(fn: Callable, jobs: Sequence, workers: int = 3) -> list:
    if workers <= 1 or len(jobs) <= 1:
        return [fn(j) for j in jobs]
    with get_context("fork").Pool(workers) as p:
        return p.map(fn, jobs, chunksize=1)


class NewestTieBest:
    """CurrentBest control: argmax mean D_pareto score, ties broken toward the newest candidate."""

    name = "current_best_newest_tie"

    def select(self, state) -> int:
        agg = state.agg_scores()
        top = max(agg)
        return max(k for k, v in enumerate(agg) if v == top)


def pareto_survivors(front: dict, agg) -> list:
    """Frontier programs left by set-cover pruning (Alg. 2), in ascending ``agg`` order (ties: first appearance)."""
    progs = sorted(dict.fromkeys(c for v in front for c in sorted(front[v])), key=lambda c: agg[c])
    dominated: set = set()
    changed = True
    while changed:
        changed = False
        for y in progs:
            if y in dominated:
                continue
            others = set(progs) - {y} - dominated
            if all(set(front[v]) & others for v in front if y in front[v]):
                dominated.add(y)
                changed = True
                break
    return [c for c in progs if c not in dominated]


def by_seed(rows: list[dict], **match) -> list[dict]:
    return sorted([r for r in rows if all(r[k] == v for k, v in match.items())], key=lambda r: r["seed"])


def group(rows: list[dict], key: str) -> dict[str, list[dict]]:
    """Rows by ``row[key]`` in first-seen order, each group sorted by seed."""
    out: dict = {}
    for r in rows:
        out.setdefault(r[key], []).append(r)
    return {k: sorted(v, key=lambda r: r["seed"]) for k, v in out.items()}


def clean(xs) -> list[float]:
    return [float(x) for x in xs if x is not None and not (isinstance(x, float) and math.isnan(x))]


def summarize(xs) -> dict:
    v = clean(xs)
    return summarize_runs(v) if v else {"mean": float("nan"), "lo": float("nan"), "hi": float("nan"), "n": 0}


def paired(a, b) -> dict:
    """Paired difference b - a over the seeds where both are present."""
    pairs = [(x, y) for x, y in zip(a, b) if x is not None and y is not None]
    return paired_diff_ci([p[0] for p in pairs], [p[1] for p in pairs]) if pairs else {}


def fmt(s: dict) -> str:
    return f"{s['mean']:.3f} [{s['lo']:.3f}, {s['hi']:.3f}]"


def fmt_diff(p: dict) -> str:
    return f"{p['mean_diff']:+.3f} [{p['lo']:+.3f}, {p['hi']:+.3f}]"


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


def pyplot():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    return plt


def ci_err(stats: Sequence[dict]) -> list[list[float]]:
    """Error-bar extents (below, above the mean) of CI summaries."""
    return [[s["mean"] - s["lo"] for s in stats], [s["hi"] - s["mean"] for s in stats]]


def save_fig(fig, path: Path, **layout) -> None:
    fig.tight_layout(**layout)
    fig.savefig(path, dpi=130)
    pyplot().close(fig)
    print(f"[saved] {path}")


def plot_curves(path: Path, budgets: Sequence[int], arms: dict[str, list[list[float]]], title: str,
                ylabel: str = "true test score of returned candidate", hline: Optional[float] = None,
                logx: bool = True) -> None:
    """Mean and 95% bootstrap CI band of per-seed curves (seeds x budgets)."""
    fig, ax = pyplot().subplots(figsize=(7, 4.2))
    for name, curves in arms.items():
        m = np.asarray(curves, float)
        stats = [summarize(m[:, j]) for j in range(m.shape[1])]
        ax.plot(budgets, [s["mean"] for s in stats], marker="o", ms=3, label=name)
        ax.fill_between(budgets, [s["lo"] for s in stats], [s["hi"] for s in stats], alpha=0.18)
    if hline is not None:
        ax.axhline(hline, color="k", ls="--", lw=0.8, label="target")
    if logx:
        ax.set_xscale("log")
    ax.set_xlabel("rollouts (metric calls)")
    ax.set_ylabel(ylabel)
    ax.set_title(title, fontsize=10)
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    save_fig(fig, path)


def bar_plot(path: Path, labels: Sequence[str], stats: Sequence[dict], title: str, ylabel: str) -> None:
    fig, ax = pyplot().subplots(figsize=(max(5, 1.1 * len(labels)), 3.8))
    ax.bar(range(len(labels)), [s["mean"] for s in stats], yerr=ci_err(stats), capsize=3, color="#4c72b0")
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=25, ha="right", fontsize=8)
    ax.set_ylabel(ylabel)
    ax.set_title(title, fontsize=10)
    ax.grid(axis="y", alpha=0.3)
    save_fig(fig, path)
