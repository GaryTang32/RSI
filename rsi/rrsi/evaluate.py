"""Evaluate(H', D, k): S_hat and C_hat of Eq. (estimate); a missing trial scores 0 in the full denominator."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional, Sequence

import numpy as np

from ..core.artifact import Artifact
from ..core.domain import Domain
from ..core.evaluate import EvalResult as CoreEvalResult
from ..core.evaluate import Evaluator
from ..core.gates import Scored
from ..core.llm import LLM


@dataclass
class TaskResult:
    rewards: list[float]                                  # missing trials -> 0.0
    weights: list[float] = field(default_factory=list)    # default 1.0 each
    tokens: list = field(default_factory=list)            # int or None per trial
    missing: int = 0
    extra: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.weights:
            self.weights = [1.0] * len(self.rewards)
        if not self.tokens:
            self.tokens = [None] * len(self.rewards)

    @property
    def mean(self) -> float:
        w = sum(self.weights)
        return (sum(r * x for r, x in zip(self.rewards, self.weights)) / w) if w else 0.0


@dataclass
class Measurement:
    """One stored evaluation (``eval.json``)."""

    job: str
    k: int
    per_task: dict                        # task_id -> TaskResult
    S: float
    C: Optional[float]
    n_expected: int
    missing: int
    extra: dict = field(default_factory=dict)       # domain aggregates (steps, error_rate, families, metrics)
    artifact_id: str = ""
    seeds: list = field(default_factory=list)
    split: str = "evolve"
    trials: dict = field(default_factory=dict)      # task_id -> [compact trial dict]

    def to_json(self) -> dict:
        return {"job": self.job, "k": self.k, "S": self.S, "C": self.C, "n_expected": self.n_expected,
                "missing": self.missing, "extra": self.extra, "artifact_id": self.artifact_id,
                "seeds": self.seeds, "split": self.split,
                "per_task": {t: asdict(r) for t, r in self.per_task.items()}, "trials": self.trials}

    @classmethod
    def from_json(cls, d: dict) -> "Measurement":
        per = {t: TaskResult(**r) for t, r in d["per_task"].items()}
        return cls(job=d["job"], k=d["k"], per_task=per, S=d["S"], C=d["C"], n_expected=d["n_expected"],
                   missing=d["missing"], extra=d.get("extra") or {}, artifact_id=d.get("artifact_id", ""),
                   seeds=d.get("seeds") or [], split=d.get("split", "evolve"), trials=d.get("trials") or {})

    def save(self, path: str | Path) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.to_json(), default=float))
        tmp.replace(p)

    @classmethod
    def load(cls, path: str | Path) -> "Measurement":
        return cls.from_json(json.loads(Path(path).read_text()))

    def task_means(self) -> dict[str, float]:
        return {t: r.mean for t, r in self.per_task.items()}

    def as_scored(self, novelty: int = 0) -> Scored:
        metrics = {k: float(v) for k, v in self.extra.items()
                   if isinstance(v, (int, float)) and not isinstance(v, bool)}
        metrics.update({"score": self.S, "cost": self.C or 0.0})
        return Scored(score=self.S, cost=self.C or 0.0, per_task=self.task_means(), metrics=metrics, novelty=novelty)


def aggregate(job: str, k: int, per_task: dict, extra: Optional[dict] = None, **kw) -> Measurement:
    """Fold per-task trial records into S_hat and C_hat."""
    num = den = 0.0
    toks: list[float] = []
    missing = 0
    for tr in per_task.values():
        for r, w in zip(tr.rewards, tr.weights):
            num += r * w
            den += w
        toks += [x for x in tr.tokens if isinstance(x, (int, float)) and x > 0]
        missing += tr.missing
    return Measurement(job=job, k=k, per_task=per_task, S=(num / den) if den else 0.0,
                       C=(sum(toks) / len(toks)) if toks else None, n_expected=len(per_task) * k,
                       missing=missing, extra=dict(extra or {}), **kw)


def relative_cost_change(C_cand: Optional[float], C_inc: Optional[float]) -> float:
    """dC = (C' - C_t) / C_t; 0 when either side has no token count."""
    if not C_cand or not C_inc:
        return 0.0
    return (C_cand - C_inc) / C_inc


def _compact_trial(tr, trace_chars: int) -> dict:
    d = tr.to_json(max_trace=trace_chars)
    try:
        json.dumps(d.get("meta"))
    except TypeError:
        d["meta"] = {k: repr(v) for k, v in (d.get("meta") or {}).items()}
    if isinstance(d.get("output"), str) and len(d["output"]) > trace_chars:
        d["output"] = d["output"][:trace_chars] + "...[truncated]"
    return d


def _is_missing(tr) -> bool:
    return bool(tr.error and str(tr.error).startswith("infra:"))


def _task_weight(trs, declared: Optional[float]) -> float:
    """``Task.meta["weight"]`` if declared, else the first graded trial's weight, else 1.0."""
    if declared is not None:
        return float(declared)
    for tr in trs:
        if not _is_missing(tr) and "weight" in (tr.meta or {}):
            return float(tr.meta["weight"])
    return 1.0


def from_core(ev: CoreEvalResult, job: str, *, seeds: Sequence[int], trace_chars: int = 3000,
              keep_trials: bool = True, task_weights: Optional[dict] = None) -> Measurement:
    """Convert an :class:`rsi.core.EvalResult` to a stored :class:`Measurement`."""
    per: dict[str, TaskResult] = {}
    trials: dict[str, list[dict]] = {}
    fam: dict[str, list[float]] = {}
    meta_sums: dict[str, list[float]] = {}
    steps, errors, n = [], 0, 0
    task_weights = task_weights or {}
    for tid, trs in ev.trials.items():
        rewards, weights, tokens, miss = [], [], [], 0
        w_task = _task_weight(trs, task_weights.get(tid))
        for tr in trs:
            is_missing = _is_missing(tr)
            miss += is_missing
            rewards.append(0.0 if is_missing else float(tr.score))
            weights.append(w_task if is_missing else float((tr.meta or {}).get("weight", w_task)))
            tokens.append(None if is_missing else (tr.tokens or None))
            steps.append(tr.steps)
            errors += bool(tr.error)
            n += 1
            for mk, mv in (tr.meta or {}).items():
                if isinstance(mv, (bool, int, float)) and mk != "weight":
                    meta_sums.setdefault(mk, []).append(float(mv))
        pad = max(0, ev.k - len(rewards))          # trials that never came back: missing, full denominator
        rewards += [0.0] * pad
        weights += [w_task] * pad
        tokens += [None] * pad
        per[tid] = TaskResult(rewards, weights, tokens, miss + pad)
        if trs:
            fam.setdefault(trs[0].family, []).append(per[tid].mean)
        if keep_trials:
            trials[tid] = [_compact_trial(tr, trace_chars) for tr in trs]
    extra = {"steps": float(np.mean(steps)) if steps else 0.0, "error_rate": errors / n if n else 0.0,
             "families": {f: float(np.mean(v)) for f, v in fam.items()}}
    extra.update({k: float(np.mean(v)) for k, v in meta_sums.items()})
    return aggregate(job, ev.k, per, extra, artifact_id=ev.artifact_id, seeds=list(seeds), split=ev.split,
                     trials=trials)


def job_seeds(job: str, k: int, run_seed: int = 0) -> list[int]:
    """A stable block of trial seeds derived from (run seed, job)."""
    h = int(hashlib.sha256(f"{run_seed}|{job}".encode()).hexdigest()[:12], 16) % 10 ** 7
    return [h * 16 + j for j in range(k)]


class Measurer:
    """Evaluate(H', D, k) on top of :class:`rsi.core.Evaluator`."""

    def __init__(self, domain: Domain, llm_task: Optional[LLM], *, workers: int = 4, run_seed: int = 0,
                 cache_dir: Optional[str | Path] = None, trace_chars: int = 3000, allow_sealed: bool = False) -> None:
        self.domain = domain
        self.llm_task = llm_task
        self.run_seed = run_seed
        self.trace_chars = trace_chars
        self.evaluator = Evaluator(domain, llm_task, workers=workers, cache_dir=cache_dir, allow_sealed=allow_sealed)

    @property
    def n_rollouts(self) -> int:
        return self.evaluator.n_rollouts

    def measure(self, artifact: Artifact, job: str, k: int, split: str = "evolve",
                seeds: Optional[Sequence[int]] = None, keep_trials: bool = True) -> Measurement:
        seeds = list(seeds) if seeds is not None else job_seeds(job, k, self.run_seed)
        ev = self.evaluator.evaluate(artifact, split, k, seeds=seeds)
        weights = {tid: t.meta["weight"] for tid, t in self.domain.tasks.tasks.items()
                   if tid in ev.trials and isinstance(t.meta, dict) and "weight" in t.meta}
        return from_core(ev, job, seeds=seeds, trace_chars=self.trace_chars, keep_trials=keep_trials,
                         task_weights=weights)
