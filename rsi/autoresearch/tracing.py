"""Per-experiment tracing for the autoresearch loop (``rsi.trace`` event schema).

Autoresearch has no reflective analysis step and no critic LLM, so the uniform
event kinds map onto one experiment (= one ``round``) as follows:

=============  =====================================================================
run_start      config, task/metric/direction/budget, keep rule, agent, seed artifact
baseline       the unmodified files run as-is (status keep), raw run values
noise          NoiseCalibrator band from baseline re-runs (only if ``noise_runs``)
round_start    loop state before the agent turn: incumbent, best, keeps, budgets,
               counters, invalid streak, val epoch, program version
analysis       what the agent reads before proposing (results.tsv tail, kept-commit
               log); autoresearch has no separate analysis step
proposal       one agent turn (per attempt): prompt + reply (LLM agent) or the
               scripted edit (mock agent), claimed change, ACTUAL diff vs parent;
               crash fixes are proposals too (``stage="fix"``)
critic         hardened ScopeGuard verdict (locked-file / out-of-scope / tamper scan)
eval           every training run (experiment, repeats, fixes): metric, memory,
               wall, crash/kill/over-budget, parsed summary, per-task (Domain tasks)
gate           keep-rule verdict with the numbers used (candidate / reference
               values and means, gain, min_gain, running best, noise delta)
decision       keep / discard / crash / rejected / duplicate / invalid, incumbent
               commit before and after, the results.tsv row written
state          loop state after the experiment
monitor        sealed-split scores of every new incumbent (never shown to the loop)
run_end        stop reason, counts, final vs baseline, spend
=============  =====================================================================

:class:`TaskAuditMonitor` is the :class:`rsi.trace.ShadowMonitor` analogue for
script tasks (tinylm / tabular / landscape): it calls ``task.audit`` (hidden
test_iid / test_shift) for each new incumbent and writes only to the trace.
"""
from __future__ import annotations

from typing import Any, Optional

from ..core.artifact import Artifact


class TaskAuditMonitor:
    """Write-only shadow audit of kept versions via ``task.audit`` (hidden splits).

    ``observe`` returns None; results go to the trace and to ``self.cache`` (so
    the post-hoc HiddenAudit can reuse identical-seed audits instead of paying
    for a second training run). Nothing is returned to the loop or the agent.
    """

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
        sealed: dict[str, Any] = {}
        for s in self.splits:
            if s in aud and isinstance(aud[s], (int, float)):
                sealed[s] = {"S": float(aud[s])}
        if truth is not None:
            sealed["truth"] = {"S": float(truth)}
        extra = {k: v for k, v in aud.items() if k not in sealed}
        tracer.event("monitor", round, version=name, artifact=artifact.short_id, decision_score=decision_score,
                     sealed=sealed, audit_extra=extra, metric_direction=getattr(self.task, "direction", "?"),
                     note="shadow audit on hidden splits - never shown to the loop")


class CachedAuditTask:
    """Proxy task whose ``audit`` returns cached shadow-audit results when present."""

    def __init__(self, task, cache: dict[str, dict]) -> None:
        self._task = task
        self._cache = cache

    def audit(self, artifact: Artifact, *, seed: int = 0) -> dict:
        hit = self._cache.get(artifact.id)
        return dict(hit) if hit is not None else self._task.audit(artifact, seed=seed)

    def __getattr__(self, name):
        return getattr(self._task, name)


def make_monitor(task, *, seed: int = 0):
    """ShadowMonitor for Domain tasks with sealed splits, TaskAuditMonitor for
    script/in-process tasks with hidden audit splits, else None."""
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
    """What one training run produced, for an ``eval`` event."""
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
