"""Shared plumbing for the Dream-RSI experiments (demo, E1-E10, live smoke).

* ``parse_args``: ``--llm sim|claude:<model>``, ``--seeds N``, ``--quick``, ``--workers``, ``--out``;
* ``llm_of`` / ``developer_of`` / ``agent_of``: offline mocks, or a cached headless ``claude -p``
  backend (cache: ``.rsi_cache/dream-rsi``) as discovery agent and/or policy developer;
* ``domain_of``: synthetic / sumdiff / circlepack / lasso / agentqa;
* ``pmap``: a small process pool over seeds; ``summ``/``paired``: mean + 95% bootstrap CI;
* ``save``: ``results/dream-rsi/<name>.json`` (+ optional figure).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from multiprocessing import Pool
from pathlib import Path
from typing import Callable, Optional

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from rsi.core import CachedLLM, ClaudeCLI, paired_diff_ci, spearman, summarize_runs  # noqa: E402,F401

RESULTS = ROOT / "results" / "dream-rsi"
CACHE = ROOT / ".rsi_cache" / "dream-rsi"


def parse_args(desc: str, default_seeds: int = 10, extra: Optional[Callable] = None):
    ap = argparse.ArgumentParser(description=desc)
    ap.add_argument("--llm", default="sim", help="sim (offline mocks) | claude:<model> (live, cached)")
    ap.add_argument("--seeds", type=int, default=None, help=f"number of seeds (default {default_seeds}; quick: 3)")
    ap.add_argument("--quick", action="store_true", help="few seeds / small budgets for a fast check")
    ap.add_argument("--workers", type=int, default=3, help="parallel processes (runs)")
    ap.add_argument("--out", default=None, help="output JSON path")
    if extra:
        extra(ap)
    a = ap.parse_args()
    if a.seeds is None:
        a.seeds = 3 if a.quick else default_seeds
    if a.llm != "sim":
        a.workers = 1
        if a.seeds > 2 and not a.quick:
            print("[note] live LLM: consider --seeds 1 --quick to bound cost", flush=True)
    return a


def llm_of(spec: str):
    """None for ``sim``; else a cached headless Claude backend."""
    if spec == "sim":
        return None
    kind, _, model = spec.partition(":")
    if kind != "claude":
        raise SystemExit(f"unsupported --llm {spec!r} (use sim or claude:<model>)")
    CACHE.mkdir(parents=True, exist_ok=True)
    return CachedLLM(ClaudeCLI(model or "haiku", timeout_s=300), CACHE)


def developer_of(spec: str, **kw):
    from rsi.dream import LLMPolicyDeveloper, ParametricMutator

    llm = llm_of(spec)
    if llm is None:
        return ParametricMutator(**kw)
    return LLMPolicyDeveloper(llm)


def domain_of(name: str, seed: int = 0, **kw):
    from rsi.domains.discovery import (CirclePackingDomain, LassoPathDomain, SumDiffDomain, SyntheticConfig,
                                       SyntheticDomain)

    if name == "synthetic":
        return SyntheticDomain(SyntheticConfig(seed=seed, **kw))
    if name == "sumdiff":
        return SumDiffDomain(sandboxed=kw.pop("sandboxed", False), **kw)
    if name == "circlepack":
        return CirclePackingDomain(sandboxed=kw.pop("sandboxed", False), **kw)
    if name == "lasso":
        return LassoPathDomain(sandboxed=kw.pop("sandboxed", False), **kw)
    raise KeyError(name)


def agent_of(domain, spec: str):
    """The frozen discovery agent: the domain's mock agent offline, an LLM editor live
    (synthetic worlds always use their simulated agent)."""
    from rsi.dream import EditorAgent

    llm = llm_of(spec)
    if llm is None or not hasattr(domain, "program_file"):
        return domain.mock_agent()
    return EditorAgent(llm, editable=[domain.program_file])


def pmap(fn, jobs, workers: int = 3):
    jobs = list(jobs)
    if workers <= 1 or len(jobs) <= 1:
        return [fn(j) for j in jobs]
    with Pool(min(workers, len(jobs))) as p:
        return p.map(fn, jobs)


def summ(values) -> dict:
    vals = [float(v) for v in values if v is not None and np.isfinite(v)]
    return summarize_runs(vals) if vals else {"mean": None, "lo": None, "hi": None, "n": 0}


def paired(a, b) -> dict:
    """b - a over paired seeds."""
    return paired_diff_ci(a, b)


def fmt(s: dict, p: int = 4) -> str:
    if s.get("mean") is None:
        return "n/a"
    return f"{s['mean']:.{p}f} [{s['lo']:.{p}f}, {s['hi']:.{p}f}]"


def save(name: str, payload: dict, out: Optional[str] = None) -> Path:
    RESULTS.mkdir(parents=True, exist_ok=True)
    p = Path(out) if out else RESULTS / f"{name}.json"
    payload = {"experiment": name, "created": time.strftime("%Y-%m-%d %H:%M:%S"), **payload}
    p.write_text(json.dumps(payload, indent=1, default=_default))
    print(f"[saved] {p}", flush=True)
    return p


def _default(o):
    if isinstance(o, (np.floating, np.integer)):
        return o.item()
    if isinstance(o, np.ndarray):
        return o.tolist()
    return str(o)


def figure(name: str):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    RESULTS.mkdir(parents=True, exist_ok=True)
    return plt, RESULTS / f"{name}.png"


def best_at(curve, calls: float):
    """Best score reached with at most ``calls`` cumulative agent calls (curve = [(cum_calls, best)])."""
    vals = [b for c, b in curve if c <= calls + 1e-9]
    return vals[-1] if vals else None


def calls_to(curve, target: float) -> Optional[float]:
    for c, b in curve:
        if b is not None and b >= target - 1e-12:
            return float(c)
    return None


os.environ.setdefault("OMP_NUM_THREADS", "1")
