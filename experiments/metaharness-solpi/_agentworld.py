"""Helpers for the SoL-Pi mechanism experiments (S1-S4, S7) on AgentWorld."""
from __future__ import annotations

import numpy as np

from _common import live_llm
from rsi.core import Evaluator
from rsi.domains.agentworld import MockAgentLLM, make_domain

SPLITS = ("evolve", "ood")          # training families + held-out families (report-only measurement)


def backend(args, profile: str):
    """Agent backend: offline MockAgent profile, or a live LLM agent (tiny runs only)."""
    if args.live:
        return live_llm(args.llm)
    return MockAgentLLM(profile)


def domain(args, seed: int, **kw):
    if args.live:
        kw = {**kw, "n_train": 1, "n_final": 1, "n_accept": 1, "n_test": 0, "subtasks": (2, 2), "max_turns": 30}
    return make_domain(seed=seed, **kw)


def trials_of(dom, llm, art, splits=SPLITS, k: int = 1) -> list:
    ev = Evaluator(dom, llm, workers=1, allow_sealed=True)
    out = []
    for sp in splits:
        if sp in dom.tasks.splits and dom.tasks.splits[sp]:
            r = ev.evaluate(art, sp, k=k)
            out += [t for trs in r.trials.values() for t in trs]
    return out


def row(t) -> dict:
    m = t.meta or {}
    return {"task": t.task_id, "seed": t.seed, "family": t.family, "score": t.score, "tokens": t.tokens,
            "cost": t.cost_usd, "requests": t.steps, "cache_read": m.get("cache_read", 0),
            "cache_write": m.get("cache_write", 0), "output": m.get("output", 0), "compactions": m.get("compactions", 0),
            "overflow": bool(m.get("overflow")), "triggers": m.get("triggers", {}), "oracle": m.get("oracle", {}),
            "audit": m.get("audit", {}), "max_context": m.get("max_context", 0)}


def agg(rows: list[dict]) -> dict:
    if not rows:
        return {}
    ks = ("score", "tokens", "cost", "requests", "cache_read", "cache_write", "output", "compactions")
    out = {k: float(np.mean([r[k] for r in rows])) for k in ks}
    out["overflow_rate"] = float(np.mean([r["overflow"] for r in rows]))
    out["n"] = len(rows)
    return out


def rel(base: dict, cand: dict, k: str) -> float:
    return (cand[k] - base[k]) / base[k] if base.get(k) else float("nan")
