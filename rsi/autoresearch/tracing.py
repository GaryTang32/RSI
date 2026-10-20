"""Write-only tracing helpers: shadow audits of new incumbents and the per-run ``eval`` payload."""
from __future__ import annotations

from typing import Any, Optional

from ..core.artifact import Artifact


class TaskAuditMonitor:
    """Shadow audit of each new incumbent via ``task.audit``; results go to the trace and ``cache`` only."""

    def __init__(self, task, seed: int = 0) -> None:
        self.task = task
        self.seed = seed
        self.seen: set[str] = set()
        self.cache: dict[str, dict] = {}

    @property
    def splits(self) -> list[str]:
        return list(getattr(self.task, "audit_splits", ()) or ())

    def observe(self, tracer, round: int, name: str, artifact: Artifact,
                decision_score: Optional[float] = None) -> None:
        if artifact.id in self.seen:
            return
        self.seen.add(artifact.id)
        aud = dict(self.task.audit(artifact, seed=self.seed) or {})
        self.cache[artifact.id] = dict(aud)
        truth = self.task.truth(artifact)
        sealed: dict[str, Any] = {s: {"S": float(aud[s])} for s in self.splits
                                  if s in aud and isinstance(aud[s], (int, float))}
        if truth is not None:
            sealed["truth"] = {"S": float(truth)}
        extra = {k: v for k, v in aud.items() if k not in sealed}
        tracer.event("monitor", round, version=name, artifact=artifact.short_id, decision_score=decision_score,
                     sealed=sealed, audit_extra=extra, metric_direction=getattr(self.task, "direction", "?"),
                     note="shadow audit on hidden splits - never shown to the loop")


class CachedAuditTask:
    """Task proxy whose ``audit`` returns cached shadow-audit results when present."""

    def __init__(self, task, cache: dict[str, dict]) -> None:
        self._task = task
        self._cache = cache

    def audit(self, artifact: Artifact, *, seed: int = 0) -> dict:
        hit = self._cache.get(artifact.id)
        return dict(hit) if hit is not None else self._task.audit(artifact, seed=seed)

    def __getattr__(self, name):
        return getattr(self._task, name)


def make_monitor(task, *, seed: int = 0):
    """ShadowMonitor for Domain tasks with sealed splits, TaskAuditMonitor for tasks with audit splits, else None."""
    from .task import DomainResearchTask

    if isinstance(task, DomainResearchTask):
        splits = [s for s in ("holdout", "ood") if s in task.domain.tasks.splits]
        if not splits:
            return None
        from ..trace import ShadowMonitor

        return ShadowMonitor(task.domain, task.llm, splits=splits, k=task.k, workers=task.workers)
    if getattr(task, "audit_splits", None):
        data_dirs = getattr(task, "data_dirs", None)
        if data_dirs is not None and "audit" not in data_dirs:
            return None
        return TaskAuditMonitor(task, seed=seed)
    return None


def outcome_json(outcome, max_log: int = 1500) -> dict:
    if outcome is None:
        return {}
    d = {"metric": outcome.metric, "memory_gb": outcome.memory_gb, "wall_s": round(outcome.wall_s, 3),
         "seed": outcome.seed, "crashed": outcome.crashed, "crash_reason": outcome.crash_reason,
         "killed": outcome.killed, "over_budget": outcome.over_budget, "summary_lines": outcome.summary}
    rec = getattr(outcome, "record", None)
    if rec:
        d["record"] = {k: v for k, v in rec.items() if k != "audit"}
    meta = dict(getattr(outcome, "meta", {}) or {})
    if meta:
        d["meta"] = meta
    if outcome.crashed:
        d["log_tail"] = outcome.tail(30)[-max_log:]
    return d
