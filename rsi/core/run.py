"""Shared run plumbing: budgets, stop conditions, results, transfer reports.

Every method's ``run(...)`` returns an :class:`ImprovementResult`, so experiment
scripts can compare methods uniformly and :func:`transfer_report` can score the
winner on the splits the loop never saw.
"""
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
    """Stop conditions. Any limit left as None is ignored. A file named ``STOP``
    in ``stop_dir`` halts the loop at the next round boundary."""

    max_rounds: Optional[int] = None
    max_rollouts: Optional[int] = None          # fresh task rollouts (GEPA's metric calls)
    max_usd: Optional[float] = None             # dollars across all LLM roles
    max_wall_s: Optional[float] = None
    stop_dir: Optional[str] = None
    _t0: float = field(default_factory=time.time, repr=False)

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
        return {
            "method": self.method, "rounds": len(self.trajectory), "stop_reason": self.stop_reason,
            "baseline": self.baseline.short_id, "best": self.best.short_id,
            "first": self.trajectory[0] if self.trajectory else None,
            "last": self.trajectory[-1] if self.trajectory else None, "usage": self.usage.get("_total"),
        }

    def save(self, out_dir: Optional[str | Path] = None) -> Path:
        d = Path(out_dir or self.out_dir or ".")
        d.mkdir(parents=True, exist_ok=True)
        self.best.to_dir(d / "best_artifact", clean=True)
        (d / "trajectory.json").write_text(json.dumps(self.trajectory, indent=1, default=float))
        (d / "summary.json").write_text(json.dumps({**self.summary(), "usage": self.usage, "meta": self.meta},
                                                   indent=1, default=str))
        return d


def usd_of(*llms: Optional[LLM]) -> float:
    return float(sum(l.meter.total().cost_usd for l in llms if l is not None))


def transfer_report(
    domain: Domain,
    llm: Optional[LLM],
    arms: dict[str, Artifact],
    *,
    splits: Sequence[str] = ("evolve", "holdout", "ood"),
    k: int = 1,
    workers: int = 8,
    reference: Optional[str] = None,
    cache_dir: Optional[str] = None,
) -> dict[str, Any]:
    """Evaluate each arm unchanged on every split (unsealing them - this is the
    final, report-only step) and give paired differences against ``reference``
    (default: the first arm, usually the starting artifact)."""
    ev = Evaluator(domain, llm, workers=workers, allow_sealed=True, cache_dir=cache_dir)
    reference = reference or next(iter(arms))
    out: dict[str, Any] = {"splits": {}, "reference": reference}
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
                         "vs_reference": cmp}
        out["splits"][split] = row
    unseen = [s for s in splits if s in ("holdout", "ood") and s in out["splits"]]
    if unseen:
        out["unseen_mean"] = {name: float(np.mean([out["splits"][s][name]["S"] for s in unseen])) for name in arms}
    return out
