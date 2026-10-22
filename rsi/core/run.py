"""Shared run plumbing: budgets, the :class:`ImprovementResult` every method returns, and transfer reports."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence

import numpy as np

from .artifact import Artifact
from .domain import Domain
from .evaluate import EvalResult, Evaluator
from .ledger import Ledger
from .llm import LLM
from .stats import paired_diff_ci


@dataclass
class Budget:
    """Stop conditions; a None limit is ignored and a ``STOP`` file in ``stop_dir`` halts at the next round."""

    max_rounds: Optional[int] = None
    max_rollouts: Optional[int] = None          # fresh task rollouts (GEPA's metric calls)
    max_usd: Optional[float] = None             # dollars across all LLM roles
    max_wall_s: Optional[float] = None
    stop_dir: Optional[str] = None
    _t0: float = field(default_factory=time.time, repr=False)

    def credit(self, seconds: float) -> None:
        """Give back wall-clock time spent on work that must not count against the budget (e.g. auditing)."""
        self._t0 += max(0.0, float(seconds))

    def exhausted(self, *, rounds: int = 0, rollouts: int = 0, usd: float = 0.0) -> Optional[str]:
        if self.max_rounds is not None and rounds >= self.max_rounds:
            return "max_rounds"
        if self.max_rollouts is not None and rollouts >= self.max_rollouts:
            return "max_rollouts"
        if self.max_usd is not None and usd >= self.max_usd:
            return "max_usd"
        if self.max_wall_s is not None and time.time() - self._t0 >= self.max_wall_s:
            return "max_wall_s"
        if self.stop_dir and Path(self.stop_dir, "STOP").exists():
            return "stop_file"
        return None


@dataclass
class ImprovementResult:
    method: str
    baseline: Artifact
    best: Artifact
    ledger: Ledger
    trajectory: list[dict] = field(default_factory=list)   # one row per round
    usage: dict = field(default_factory=dict)
    stop_reason: str = ""
    out_dir: Optional[str] = None
    meta: dict = field(default_factory=dict)

    def summary(self) -> dict:
        return {"method": self.method, "rounds": len(self.trajectory), "stop_reason": self.stop_reason,
                "baseline": self.baseline.short_id, "best": self.best.short_id,
                "first": self.trajectory[0] if self.trajectory else None,
                "last": self.trajectory[-1] if self.trajectory else None, "usage": self.usage.get("_total")}

    def save(self, out_dir: Optional[str | Path] = None) -> Path:
        """Write ``best_artifact/``, ``trajectory.json`` and ``summary.json`` under ``out_dir``."""
        d = Path(out_dir or self.out_dir or ".")
        d.mkdir(parents=True, exist_ok=True)
        self.best.to_dir(d / "best_artifact", clean=True)
        (d / "trajectory.json").write_text(json.dumps(self.trajectory, indent=1, default=_jsonable))
        (d / "summary.json").write_text(json.dumps({**self.summary(), "usage": self.usage, "meta": self.meta},
                                                   indent=1, default=str))
        return d


def _jsonable(o: Any) -> Any:
    """JSON fallback: a float if it converts (numpy scalars), else ``tolist()``, else ``to_json()``, else text."""
    try:
        return float(o)
    except (TypeError, ValueError):
        pass
    if hasattr(o, "tolist"):
        return o.tolist()
    if hasattr(o, "to_json"):
        return o.to_json()
    return str(o)


def usd_of(*llms: Optional[LLM]) -> float:
    return float(sum(llm.meter.total().cost_usd for llm in llms if llm is not None))


def transfer_report(domain: Domain, llm: Optional[LLM], arms: dict[str, Artifact], *,
                    splits: Sequence[str] = ("evolve", "holdout", "ood"), k: int = 1, workers: int = 8,
                    reference: Optional[str] = None, cache_dir: Optional[str] = None,
                    seed_offset: int = 0) -> dict[str, Any]:
    """Report-only step: score each arm unchanged on every split, paired against ``reference``."""
    # loops use seeds from 0: offset 0 repeats (with a cached LLM, replays) their samples, e.g. 100 re-scores afresh
    ev = Evaluator(domain, llm, workers=workers, allow_sealed=True, cache_dir=cache_dir, seed_offset=seed_offset)
    reference = reference or next(iter(arms))
    out: dict[str, Any] = {"splits": {}, "reference": reference, "k": k, "seed_offset": seed_offset}
    for split in splits:
        if split not in domain.tasks.splits:
            continue
        res: dict[str, EvalResult] = {name: ev.evaluate(a, split, k) for name, a in arms.items()}
        ref = res[reference].task_scores()
        row = {}
        for name, r in res.items():
            ts = r.task_scores()
            ids = sorted(ts)
            cmp = paired_diff_ci([ref[i] for i in ids], [ts[i] for i in ids]) if name != reference else None
            row[name] = {"S": r.score, "C": r.cost, "steps": r.steps, "families": r.family_scores(),
                         "missing": r.n_missing, "tasks": ts, "vs_reference": cmp}
        out["splits"][split] = row
    unseen = [s for s in splits if s in ("holdout", "ood") and s in out["splits"]]
    if unseen:
        out["unseen_mean"] = {name: float(np.mean([out["splits"][s][name]["S"] for s in unseen])) for name in arms}
    return out
