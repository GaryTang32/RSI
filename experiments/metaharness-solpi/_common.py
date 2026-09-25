"""Shared plumbing for the Meta-Harness (M1-M5) and SoL-Pi (S1-S7) experiments.

* ``parse_args``: ``--llm sim|claude:<model>``, ``--seeds N``, ``--quick``, ``--workers``, ``--out``;
* ``proposer_llm`` / ``live_llm``: cached headless ``claude -p`` (cache under ``.rsi_cache/metaharness-solpi``);
* ``pool_map``: independent (seed, arm) jobs in forked worker processes (serial for live LLMs);
* ``summarize`` / ``paired`` / ``save`` / ``fmt``: mean + 95% bootstrap CI via ``rsi.core.stats``;
  JSON outputs under ``results/metaharness-solpi``.
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

SLUG = "metaharness-solpi"
RESULTS = ROOT / "results" / SLUG
CACHE = ROOT / ".rsi_cache" / SLUG
RUNS = Path(os.environ.get("RSI_RUNS", "/tmp")) / f"rsi_{SLUG}_runs"


def parse_args(desc: str, default_seeds: int = 10, extra: Optional[Callable] = None):
    ap = argparse.ArgumentParser(description=desc)
    ap.add_argument("--llm", default="sim", help="sim (offline mocks) | claude:<model> (live, cached)")
    ap.add_argument("--seeds", type=int, default=None, help=f"number of seeds (default {default_seeds}; quick: 3)")
    ap.add_argument("--quick", action="store_true", help="few seeds / small budgets for a fast check")
    ap.add_argument("--workers", type=int, default=3, help="parallel processes")
    ap.add_argument("--out", default=None, help="output JSON path")
    if extra:
        extra(ap)
    a = ap.parse_args()
    a.live = a.llm != "sim"
    if a.seeds is None:
        a.seeds = 1 if a.live else (3 if a.quick else default_seeds)
    if a.live:
        a.workers = 1
        print(f"[note] live LLM {a.llm}: small budgets, cached under {CACHE}", flush=True)
    return a


def fresh_dir(*parts: str) -> Path:
    """A clean run directory under RUNS (experiments never resume a finalised store)."""
    import shutil
    d = RUNS.joinpath(*parts)
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True, exist_ok=True)
    return d


def live_llm(spec: str, timeout_s: float = 300.0):
    kind, _, model = spec.partition(":")
    if kind != "claude":
        raise SystemExit(f"unsupported --llm {spec!r} (use sim or claude:<model>)")
    CACHE.mkdir(parents=True, exist_ok=True)
    return CachedLLM(ClaudeCLI(model or "haiku", timeout_s=timeout_s), CACHE)


def pool_map(fn: Callable, jobs: Sequence, workers: int = 3) -> list:
    if workers <= 1 or len(jobs) <= 1:
        return [fn(j) for j in jobs]
    ctx = get_context("fork")
    with ctx.Pool(min(workers, len(jobs))) as p:
        return p.map(fn, jobs, chunksize=1)


def clean(xs) -> list[float]:
    return [float(x) for x in xs if x is not None and not (isinstance(x, float) and math.isnan(x))]


def summarize(xs) -> dict:
    v = clean(xs)
    return summarize_runs(v) if v else {"mean": float("nan"), "lo": float("nan"), "hi": float("nan"), "n": 0}


def paired(a, b) -> dict:
    """Paired difference b - a over seeds (mean + 95% bootstrap CI)."""
    pairs = [(x, y) for x, y in zip(a, b) if x is not None and y is not None]
    if not pairs:
        return {}
    return paired_diff_ci([p[0] for p in pairs], [p[1] for p in pairs])


def fmt(s: dict, p: int = 3) -> str:
    return f"{s['mean']:.{p}f} [{s['lo']:.{p}f}, {s['hi']:.{p}f}]"


def _default(x):
    if isinstance(x, (np.floating, np.integer)):
        return x.item()
    if isinstance(x, np.ndarray):
        return x.tolist()
    if isinstance(x, (set, tuple)):
        return list(x)
    return repr(x)


def save(name: str, payload: dict, out: Optional[str] = None) -> Path:
    RESULTS.mkdir(parents=True, exist_ok=True)
    p = Path(out) if out else RESULTS / f"{name}.json"
    payload.setdefault("generated_at", time.strftime("%Y-%m-%d %H:%M:%S"))
    p.write_text(json.dumps(payload, indent=1, default=_default))
    print(f"[saved] {p}")
    return p


def plt():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as _plt
    return _plt


def table(rows: list[list], header: list[str]) -> str:
    w = [max(len(str(r[i])) for r in rows + [header]) for i in range(len(header))]
    line = lambda r: "  ".join(str(c).ljust(w[i]) for i, c in enumerate(r))
    return "\n".join([line(header), line(["-" * x for x in w])] + [line(r) for r in rows])
