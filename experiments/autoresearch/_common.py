"""Shared helpers for the autoresearch experiment scripts."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from rsi.core import CachedLLM, ClaudeCLI, summarize_runs  # noqa: E402

RESULTS = Path(os.environ.get("RSI_AR_RESULTS", ROOT / "results" / "autoresearch"))
CACHE = ROOT / ".rsi_cache" / "autoresearch"
SCRATCH = Path(os.environ.get("RSI_AR_SCRATCH", "/tmp/rsi_autoresearch_runs"))


def parser(description: str, seeds: int = 3) -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=description)
    ap.add_argument("--llm", default="sim",
                    help="sim (scripted offline agent) | claude:haiku (live research agent) | scripted (offline "
                         "MockLLM through the same LLM-agent code path as a live run)")
    ap.add_argument("--seeds", type=int, default=seeds)
    ap.add_argument("--quick", action="store_true", help="tiny settings for a smoke check")
    ap.add_argument("--workers", type=int, default=2, help="parallel seeds (processes)")
    return ap


def propose_llm(spec: str, pool=None, seed: int = 0):
    """None for the offline scripted agent (``sim``); a cached headless Claude for
    ``claude:<model>``; for ``scripted``, an offline MockLLM that answers the LLM
    agent's prompts with edits from ``pool`` (tests the live code path for free)."""
    if spec in ("sim", "mock", "", None):
        return None
    if spec == "scripted":
        from rsi.autoresearch import scripted_llm

        if pool is None:
            raise SystemExit("--llm scripted needs the task's edit pool")
        return scripted_llm(pool, seed=seed)
    kind, _, model = spec.partition(":")
    if kind != "claude":
        raise SystemExit(f"unsupported --llm {spec!r}; use sim, scripted or claude:<model>")
    return CachedLLM(ClaudeCLI(model or "haiku", timeout_s=300), CACHE)


def is_live(spec: str) -> bool:
    return spec not in ("sim", "mock", "", None)


def suffix(spec: str, quick: bool) -> str:
    """Result-name suffix: '' (offline), '_live' (real model) or '_scripted' (offline MockLLM), + '_quick'."""
    kind = "" if not is_live(spec) else "_scripted" if spec == "scripted" else "_live"
    return kind + ("_quick" if quick else "")


def research_agent(spec: str, pool, *, seed: int = 0, **mock_kw):
    """``(agent, llms)``: the scripted MockResearchAgent for ``--llm sim``, else an
    LLMResearchAgent (RewriteEditor) on the model from :func:`propose_llm`."""
    from rsi.autoresearch import LLMResearchAgent, MockResearchAgent
    from rsi.core import RewriteEditor

    llm = propose_llm(spec, pool=pool, seed=seed)
    if llm is None:
        return MockResearchAgent(pool, seed=seed, **mock_kw), []
    return LLMResearchAgent(RewriteEditor(llm)), [llm]


def usage_of(llms) -> dict:
    """Summed LLM usage (calls, tokens, dollars) of the given backends."""
    tot = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
    for l in llms:
        t = l.meter.total()
        tot["calls"] += t.calls
        tot["input_tokens"] += t.input_tokens
        tot["output_tokens"] += t.output_tokens
        tot["cost_usd"] += t.cost_usd
    return tot


def ci(values) -> dict:
    vals = [float(v) for v in values if v is not None and v == v]
    return summarize_runs(vals) if vals else {"mean": float("nan"), "lo": float("nan"), "hi": float("nan"), "n": 0}


def write(name: str, payload: dict) -> Path:
    RESULTS.mkdir(parents=True, exist_ok=True)
    p = RESULTS / f"{name}.json"
    payload = {"experiment": name, "written_at": time.strftime("%Y-%m-%d %H:%M:%S"), **payload}
    p.write_text(json.dumps(payload, indent=1, default=_default))
    print(f"wrote {p}")
    return p


def _default(o):
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    return str(o)


def pool_map(fn, items, workers: int):
    """Run ``fn`` over ``items`` in a process pool (fork) or serially."""
    if workers <= 1 or len(items) <= 1:
        return [fn(x) for x in items]
    import multiprocessing as mp

    with mp.get_context("fork").Pool(min(workers, len(items))) as pool:
        return pool.map(fn, items)


def plt():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as p

    return p


def running_best(traj: list[dict], direction: str = "min") -> list[float]:
    out, best = [], None
    for r in traj:
        if r["status"] == "keep" and r["metric"] is not None:
            if best is None or (r["metric"] < best if direction == "min" else r["metric"] > best):
                best = r["metric"]
        out.append(best)
    return out
