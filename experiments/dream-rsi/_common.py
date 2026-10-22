"""Shared helpers for the Dream-RSI experiments: CLI, LLM backends, domains, recorded searches, pool, stats, output."""
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

os.environ.setdefault("OMP_NUM_THREADS", "1")

RESULTS = ROOT / "results" / "dream-rsi"
CACHE = ROOT / ".rsi_cache" / "dream-rsi"
RUN_TAG = ""  # live runs save to ``<name>__<llm>.json`` so they never overwrite the offline result; set by parse_args


def tagged(name: str) -> str:
    return name if (not RUN_TAG or name.startswith("live_smoke")) else f"{name}__{RUN_TAG}"


def parse_args(desc: str, default_seeds: int = 10, extra: Optional[Callable] = None):
    ap = argparse.ArgumentParser(description=desc)
    ap.add_argument("--llm", default="sim", help="sim (offline mocks) | claude:<model> (live, cached)")
    ap.add_argument("--seeds", type=int, default=None, help=f"number of seeds (default {default_seeds}; quick: 3)")
    ap.add_argument("--quick", action="store_true", help="few seeds / small budgets for a fast check")
    ap.add_argument("--workers", type=int, default=3, help="parallel processes (runs)")
    ap.add_argument("--out", default=None, help="output JSON path")
    ap.add_argument("--cache", default=None,
                    help="LLM cache directory for --llm claude:* (default .rsi_cache/dream-rsi; pass a fresh "
                         "directory for a from-scratch run)")
    if extra:
        extra(ap)
    a = ap.parse_args()
    if a.seeds is None:
        a.seeds = 3 if a.quick else default_seeds
    global CACHE, RUN_TAG
    if a.cache:
        CACHE = Path(a.cache).resolve()
    if a.llm != "sim":
        RUN_TAG = a.llm.replace(":", "-")
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
    return ParametricMutator(**kw) if llm is None else LLMPolicyDeveloper(llm)


def llm_policies(spec: str, worlds, *, n: int = 2, W: int = 4, fallback=(6, 4), base_code: Optional[str] = None,
                 objective=None, root_mode: str = "earliest", hard_max=(16, 16)) -> dict:
    """``{"llm_1": code, ...}`` from a short dreaming chain of the LLM developer on ``worlds`` (empty offline).

    Each revision starts from the strongest version so far; LLM-written code only ever runs in the subprocess sandbox.
    """
    if spec == "sim":
        return {}
    from rsi.dream import (DevContext, ReplayEvaluator, SubprocessRunner, VersionRecord, adaptive, code_of,
                           parallel_refine)

    ev = ReplayEvaluator(objective, W=W, fallback=tuple(fallback), runner=SubprocessRunner(timeout_s=30),
                         root_mode=root_mode, hard_max=tuple(hard_max))
    dev = developer_of(spec)
    inc = VersionRecord(0, base_code or code_of(adaptive()), label="incumbent")
    inc.report = ev.evaluate(inc.code, worlds)
    versions, out = [inc], {}
    for m in range(n):
        rev = dev.revise(DevContext(1, versions, [], [], code_of(parallel_refine()), "eq1", W,
                                    first_in_phase=(m == 0)), seed=m)
        if not rev.ok:
            print(f"[llm policy {m + 1}] rejected: {rev.error}", flush=True)
            continue
        rec = VersionRecord(m + 1, rev.code, ev.evaluate(rev.code, worlds), rev.change, label=f"llm_{m + 1}")
        versions.append(rec)
        out[rec.label] = rev.code
        print(f"[llm policy {m + 1}] replay V {rec.report.value:+.4f} (incumbent {inc.report.value:+.4f}): "
              f"{rev.change[:120]}", flush=True)
    return out


def runner_for(name: str) -> str:
    """Policies written by an LLM (named ``llm_*``) always run in the subprocess sandbox."""
    return "subprocess" if name.startswith("llm_") else "inprocess"


def sandbox_of(spec: str) -> str:
    """In-process for the offline mock developer's template edits, the subprocess sandbox for LLM-written policies."""
    return "inprocess" if spec == "sim" else "subprocess"


def domain_of(name: str, seed: int = 0, **kw):
    from rsi.domains.discovery import (AutocorrelationDomain, CirclePackingDomain, LassoPathDomain, SumDiffDomain,
                                       SyntheticConfig, SyntheticDomain)

    if name == "synthetic":
        return SyntheticDomain(SyntheticConfig(seed=seed, **kw))
    sandboxed = kw.pop("sandboxed", False)
    if name == "sumdiff":
        return SumDiffDomain(sandboxed=sandboxed, **kw)
    if name == "circlepack":
        return CirclePackingDomain(sandboxed=sandboxed, **kw)
    if name == "lasso":
        return LassoPathDomain(sandboxed=sandboxed, **kw)
    if name == "autocorr":
        return AutocorrelationDomain(kw.pop("variant", "phi3"), sandboxed=sandboxed, **kw)
    raise KeyError(name)


def agent_of(domain, spec: str):
    """The domain's mock agent offline; live, an LLM editor whose untrusted programs are graded in the sandbox."""
    from rsi.dream import EditorAgent

    llm = llm_of(spec)
    if llm is None or not hasattr(domain, "program_file"):
        return domain.mock_agent()
    if hasattr(domain, "sandboxed"):
        domain.sandboxed = True
    return EditorAgent(llm, editable=[domain.program_file])


def live_search(domain, W: int, grid, seed: int, policy=None, **cfg):
    """One live search (no dreaming) of ``domain`` with its mock agent; returns the run result."""
    from rsi.dream import Config, DreamRSILoop

    config = Config(rounds=1, W=W, branch_count=grid[0], refine_count=grid[1], dream=False, seed=seed,
                    **{"sandbox": "inprocess", "agent_workers": 1, **cfg})
    return DreamRSILoop(domain.as_task(), domain.mock_agent(), config=config, initial_policy=policy).run()


def record_world(domain, W: int, grid, seed: int, **cfg):
    """The world recorded by one parallel-refine search of ``domain``."""
    return live_search(domain, W, grid, seed, **cfg).meta["worlds"][0]


def online_eq1(r: dict, beta1: float, beta2: float) -> float:
    """Raw Eq. 1 of one live round (a trajectory row)."""
    return r["round_best"] - beta1 * r["N"] + beta2 * r["N"] / max(1, r["k"])


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
    p = Path(out) if out else RESULTS / f"{tagged(name)}.json"
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
    return plt, RESULTS / f"{tagged(name)}.png"


def save_figure(fig, png) -> None:
    fig.tight_layout()
    fig.savefig(png, dpi=130)


def best_at(curve, calls: float):
    """Best score reached with at most ``calls`` cumulative agent calls (curve = [(cum_calls, best)])."""
    vals = [b for c, b in curve if c <= calls + 1e-9]
    return vals[-1] if vals else None


def calls_to(curve, target: float) -> Optional[float]:
    return next((float(c) for c, b in curve if b is not None and b >= target - 1e-12), None)
