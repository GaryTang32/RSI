"""Parallel evaluation of artifacts on task splits.

``Evaluator.evaluate(artifact, split, k)`` runs every task ``k`` times and
aggregates exactly the way RRSI's ``rrsi/evaluate.py: aggregate`` does:

* ``S`` = mean over tasks of the mean trial score, where a *missing* trial
  (infrastructure failure) counts 0 with the full denominator;
* ``C`` = mean artifact tokens per trial over trials that reported tokens.

Results are cached by (artifact id, task id, seed), so re-scoring the incumbent
or replaying a run costs nothing.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional, Sequence, Union

import numpy as np

from .artifact import Artifact
from .domain import Domain, Trial
from .llm import LLM
from .tasks import Task


@dataclass
class EvalResult:
    artifact_id: str
    split: str
    trials: dict[str, list[Trial]]           # task_id -> trials (seed order)
    k: int
    n_missing: int = 0
    meta: dict = field(default_factory=dict)

    # ---- aggregates
    @property
    def task_ids(self) -> list[str]:
        return list(self.trials)

    def task_scores(self) -> dict[str, float]:
        """Per-task mean score (missing trials count 0, denominator k)."""
        out = {}
        for tid, trs in self.trials.items():
            out[tid] = float(sum(t.score for t in trs) / max(self.k, len(trs), 1))
        return out

    @property
    def score(self) -> float:
        ts = self.task_scores()
        return float(np.mean(list(ts.values()))) if ts else 0.0

    S = score  # RRSI notation

    @property
    def cost(self) -> float:
        toks = [t.tokens for trs in self.trials.values() for t in trs if t.tokens]
        return float(np.mean(toks)) if toks else 0.0

    C = cost

    @property
    def dollars(self) -> float:
        return float(sum(t.cost_usd for trs in self.trials.values() for t in trs))

    @property
    def steps(self) -> float:
        st = [t.steps for trs in self.trials.values() for t in trs]
        return float(np.mean(st)) if st else 0.0

    @property
    def n_trials(self) -> int:
        return sum(len(v) for v in self.trials.values())

    @property
    def error_rate(self) -> float:
        n = self.n_trials
        return sum(1 for trs in self.trials.values() for t in trs if t.error) / n if n else 0.0

    def family_scores(self) -> dict[str, float]:
        fam: dict[str, list[float]] = {}
        for tid, trs in self.trials.items():
            if trs:
                fam.setdefault(trs[0].family, []).append(sum(t.score for t in trs) / max(self.k, len(trs), 1))
        return {k: float(np.mean(v)) for k, v in fam.items()}

    def trial_matrix(self) -> np.ndarray:
        """tasks x k matrix of scores (missing = 0); rows follow ``task_ids``."""
        m = np.zeros((len(self.trials), self.k))
        for i, trs in enumerate(self.trials.values()):
            for j, t in enumerate(trs[: self.k]):
                m[i, j] = t.score
        return m

    def worst(self, n: int = 3) -> list[Trial]:
        """Worst trial of the ``n`` lowest-scoring tasks (RRSI analyst input)."""
        ts = self.task_scores()
        ids = sorted(ts, key=lambda i: ts[i])[:n]
        return [min(self.trials[i], key=lambda t: t.score) for i in ids if self.trials[i]]

    def best(self, n: int = 2) -> list[Trial]:
        ts = self.task_scores()
        ids = sorted(ts, key=lambda i: -ts[i])[:n]
        return [max(self.trials[i], key=lambda t: t.score) for i in ids if self.trials[i]]

    def summary(self) -> dict:
        return {
            "artifact": self.artifact_id[:10], "split": self.split, "k": self.k, "S": round(self.score, 4),
            "C": round(self.cost, 1), "steps": round(self.steps, 2), "n_tasks": len(self.trials),
            "missing": self.n_missing, "errors": round(self.error_rate, 3), "usd": round(self.dollars, 4),
        }

    def to_json(self, max_trace: int = 2000) -> dict:
        return {
            "artifact_id": self.artifact_id, "split": self.split, "k": self.k, "n_missing": self.n_missing,
            "meta": self.meta, "summary": self.summary(),
            "trials": {tid: [t.to_json(max_trace) for t in trs] for tid, trs in self.trials.items()},
        }


def _stable_repr(o) -> str:
    """JSON fallback for task fingerprints: the same across processes (no memory addresses)."""
    if hasattr(o, "to_json"):
        try:
            return json.dumps(o.to_json(), sort_keys=True, default=_stable_repr)
        except Exception:  # noqa: BLE001
            pass
    r = repr(o)
    return type(o).__qualname__ if " at 0x" in r else r


class Evaluator:
    """Runs a :class:`Domain` over tasks in a thread pool with caching.

    Parameters
    ----------
    domain, llm:
        the frozen problem and the frozen model the artifact uses.
    workers:
        parallel rollouts (LLM subprocess calls are I/O bound, so threads suffice).
    cache_dir:
        optional persistent trial cache (JSON per trial). Each entry records
        which task model (``llm.name``, ignoring ``cached:`` wrappers), domain
        (``domain.name``) and task content produced it, and an entry written
        for a different one is treated as a miss and re-run, so a shared
        ``cache_dir`` never serves one model's (or one suite's) trials to
        another. Entries without that record (older caches) are still read.
    allow_sealed:
        when False (default) evaluating a sealed split raises - use a
        :class:`TransferEvaluator`-style report step to unseal.
    """

    def __init__(
        self,
        domain: Domain,
        llm: Optional[LLM] = None,
        *,
        workers: int = 8,
        cache_dir: Optional[Union[str, Path]] = None,
        allow_sealed: bool = False,
        seed_offset: int = 0,
    ) -> None:
        self.domain = domain
        self.llm = llm
        self.workers = workers
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.allow_sealed = allow_sealed
        self.seed_offset = seed_offset
        self._mem: dict[tuple, Trial] = {}
        self._lock = threading.Lock()
        self.n_rollouts = 0            # fresh (uncached) rollouts = the budget unit of GEPA / autoresearch
        self.on_trial: list[Callable[[Artifact, Trial], None]] = []

    # ---- caching
    def _cache_path(self, aid: str, tid: str, seed: int) -> Optional[Path]:
        if not self.cache_dir:
            return None
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in tid)
        return self.cache_dir / aid[:12] / f"{safe}__{seed}.json"

    def cache_identity(self, task: Task) -> dict:
        """What besides (artifact, task id, seed) a disk-cache entry depends on."""
        name = getattr(self.llm, "name", None) if self.llm is not None else None
        while isinstance(name, str) and name.startswith("cached:"):
            name = name[len("cached:"):]   # a CachedLLM replays the same model: its trials are interchangeable
        blob = json.dumps([task.input, task.target, task.family], sort_keys=True, default=_stable_repr)
        return {"llm": name, "domain": getattr(self.domain, "name", None),
                "task": hashlib.sha256(blob.encode()).hexdigest()[:16]}

    def _get_cached(self, aid, tid, seed, ident: Optional[dict] = None) -> Optional[Trial]:
        key = (aid, tid, seed)
        with self._lock:
            if key in self._mem:
                return self._mem[key]
        p = self._cache_path(aid, tid, seed)
        if p and p.exists():
            try:
                d = json.loads(p.read_text())
                if ident is not None and d.get("_cache_identity", ident) != ident:
                    return None  # written for another task model / domain / suite: re-run (and overwrite)
                tr = Trial(**{k: v for k, v in d.items() if k in Trial.__dataclass_fields__})
            except (OSError, ValueError, TypeError, AttributeError):
                return None  # corrupt/foreign cache entry: re-run the trial (the entry is overwritten)
            with self._lock:
                self._mem[key] = tr
            return tr
        return None

    def _put(self, aid, tr: Trial, ident: Optional[dict] = None) -> None:
        with self._lock:
            self._mem[(aid, tr.task_id, tr.seed)] = tr
        p = self._cache_path(aid, tr.task_id, tr.seed)
        if p:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_name(f"{p.name}.{os.getpid()}.{threading.get_ident()}.tmp")
            d = tr.to_json(max_trace=20000)
            if ident is not None:
                d["_cache_identity"] = ident
            tmp.write_text(json.dumps(d))
            tmp.replace(p)  # atomic: a crash never leaves a half-written entry

    # ---- evaluation
    def run_one(self, artifact: Artifact, task: Task, seed: int) -> Trial:
        s = seed + self.seed_offset
        ident = self.cache_identity(task) if self.cache_dir else None
        cached = self._get_cached(artifact.id, task.id, s, ident)
        if cached is not None:
            return cached
        tr = self.domain.run(artifact, task, seed=s, llm=self.llm)
        with self._lock:
            self.n_rollouts += 1
        # Infrastructure failures (e.g. LLM backend down) are not cached so they can be retried.
        if not (tr.error and tr.error.startswith("infra:")):
            self._put(artifact.id, tr, ident)
        for cb in self.on_trial:
            cb(artifact, tr)
        return tr

    def evaluate(
        self,
        artifact: Artifact,
        split: Union[str, Sequence[Task]] = "evolve",
        k: int = 1,
        *,
        seeds: Optional[Sequence[int]] = None,
        label: Optional[str] = None,
    ) -> EvalResult:
        """Run each task of ``split`` once per seed and aggregate.

        ``split`` is a split name (sealed splits raise :class:`SealedSplitError`
        unless ``allow_sealed``) or an explicit task list reported as ``label``.
        Seeds are ``seeds`` or ``range(k)``; duplicate seeds / task ids are
        dropped so every (task, seed) slot is filled exactly once.
        """
        if isinstance(split, str):
            tasks = self.domain.tasks.split(split, allow_sealed=self.allow_sealed)
            split_name = split
        else:
            tasks = list(split)
            split_name = label or "custom"
        tasks = list({t.id: t for t in tasks}.values())
        seeds = list(dict.fromkeys(seeds)) if seeds is not None else list(range(k))  # duplicates would leave empty slots
        jobs = [(t, s) for t in tasks for s in seeds]
        results: dict[str, list[Optional[Trial]]] = {t.id: [None] * len(seeds) for t in tasks}
        idx = {s: j for j, s in enumerate(seeds)}

        def work(job):
            t, s = job
            return t.id, s, self.run_one(artifact, t, s)

        if self.workers <= 1 or len(jobs) <= 1:
            outs = [work(j) for j in jobs]
        else:
            with ThreadPoolExecutor(max_workers=self.workers) as ex:
                outs = list(ex.map(work, jobs))
        n_missing = 0
        for tid, s, tr in outs:
            if tr.error and tr.error.startswith("infra:"):
                n_missing += 1   # a missing trial scores the domain's failure score (0) over the full denominator
                tr = Trial(task_id=tid, seed=s, score=float(getattr(self.domain, "failure_score", 0.0)),
                           error=tr.error, family=tr.family)
            results[tid][idx[s]] = tr
        trials = {tid: [t for t in trs if t is not None] for tid, trs in results.items()}
        return EvalResult(artifact.id, split_name, trials, k=len(seeds), n_missing=n_missing)

    def evaluate_many(self, artifacts: Iterable[Artifact], split="evolve", k: int = 1) -> list[EvalResult]:
        return [self.evaluate(a, split, k) for a in artifacts]
