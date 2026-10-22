"""Shared plumbing for the Meta-Harness (M*) and SoL-Pi (S*) experiments: CLI, cached live LLM, pool, stats, I/O."""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
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


def parse_args(desc: str, default_seeds: int = 10, extra: Optional[Callable] = None) -> argparse.Namespace:
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
    """An empty run directory under RUNS (experiments never resume a finalised store)."""
    d = RUNS.joinpath(*parts)
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True, exist_ok=True)
    return d


def live_llm(spec: str, timeout_s: float = 300.0) -> CachedLLM:
    kind, _, model = spec.partition(":")
    if kind != "claude":
        raise SystemExit(f"unsupported --llm {spec!r} (use sim or claude:<model>)")
    CACHE.mkdir(parents=True, exist_ok=True)
    return CachedLLM(ClaudeCLI(model or "haiku", timeout_s=timeout_s), CACHE)


def pool_map(fn: Callable, jobs: Sequence, workers: int = 3) -> list:
    if workers <= 1 or len(jobs) <= 1:
        return [fn(j) for j in jobs]
    with get_context("fork").Pool(min(workers, len(jobs))) as p:
        return p.map(fn, jobs, chunksize=1)


def summarize(xs) -> dict:
    """Mean + 95% bootstrap CI over the values that are neither None nor NaN."""
    v = [float(x) for x in xs if x is not None and not (isinstance(x, float) and math.isnan(x))]
    return summarize_runs(v) if v else {"mean": float("nan"), "lo": float("nan"), "hi": float("nan"), "n": 0}


def paired(a, b) -> dict:
    """Paired difference b - a over the seeds where both are present."""
    pairs = [(x, y) for x, y in zip(a, b) if x is not None and y is not None]
    if not pairs:
        return {}
    return paired_diff_ci([x for x, _ in pairs], [y for _, y in pairs])


def candidate_rows(store, keys: tuple[str, ...]) -> list[dict]:
    """One row per stored candidate: its name, the given meta keys, search score and context cost."""
    return [{"name": n, **{k: store.meta(n).get(k) for k in keys},
             "search": (store.scores(n) or {}).get("score"), "context": (store.scores(n) or {}).get("context_cost")}
            for n in store.names()]


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


def figure_path(name: str, args) -> Path:
    """Next to ``--out`` if given, else ``results/<slug>/<name>[_live].png`` (live never overwrites offline)."""
    if getattr(args, "out", None):
        return Path(args.out).with_suffix(".png")
    RESULTS.mkdir(parents=True, exist_ok=True)
    return RESULTS / f"{name}{'_live' if getattr(args, 'live', False) else ''}.png"


def plt():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as _plt
    return _plt


def table(rows: list[list], header: list[str]) -> str:
    widths = [max(len(str(r[i])) for r in rows + [header]) for i in range(len(header))]

    def line(r: list) -> str:
        return "  ".join(str(c).ljust(widths[i]) for i, c in enumerate(r))

    return "\n".join([line(header), line(["-" * w for w in widths])] + [line(r) for r in rows])
