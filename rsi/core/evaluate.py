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

import json
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


class Evaluator:
    """Runs a :class:`Domain` over tasks in a thread pool with caching.

    Parameters
    ----------
    domain, llm:
        the frozen problem and the frozen model the artifact uses.
    workers:
        parallel rollouts (LLM subprocess calls are I/O bound, so threads suffice).
    cache_dir:
        optional persistent trial cache (JSON per trial).
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

    def _get_cached(self, aid, tid, seed) -> Optional[Trial]:
        key = (aid, tid, seed)
        with self._lock:
            if key in self._mem:
                return self._mem[key]
        p = self._cache_path(aid, tid, seed)
        if p and p.exists():
            d = json.loads(p.read_text())
            tr = Trial(**d)
            with self._lock:
                self._mem[key] = tr
            return tr
        return None

    def _put(self, aid, tr: Trial) -> None:
        with self._lock:
            self._mem[(aid, tr.task_id, tr.seed)] = tr
        p = self._cache_path(aid, tr.task_id, tr.seed)
        if p:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(tr.to_json(max_trace=20000)))

    # ---- evaluation
    def run_one(self, artifact: Artifact, task: Task, seed: int) -> Trial:
        s = seed + self.seed_offset
        cached = self._get_cached(artifact.id, task.id, s)
        if cached is not None:
            return cached
        tr = self.domain.run(artifact, task, seed=s, llm=self.llm)
        with self._lock:
            self.n_rollouts += 1
        # Infrastructure failures (e.g. LLM backend down) are not cached so they can be retried.
        if not (tr.error and tr.error.startswith("infra:")):
            self._put(artifact.id, tr)
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
        if isinstance(split, str):
            tasks = self.domain.tasks.split(split, allow_sealed=self.allow_sealed)
            split_name = split
        else:
            tasks = list(split)
            split_name = label or "custom"
        seeds = list(seeds) if seeds is not None else list(range(k))
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
                n_missing += 1
                tr = Trial(task_id=tid, seed=s, score=0.0, error=tr.error, family=tr.family)
            results[tid][idx[s]] = tr
        trials = {tid: [t for t in trs if t is not None] for tid, trs in results.items()}
        return EvalResult(artifact.id, split_name, trials, k=len(seeds), n_missing=n_missing)

    def evaluate_many(self, artifacts: Iterable[Artifact], split="evolve", k: int = 1) -> list[EvalResult]:
        return [self.evaluate(a, split, k) for a in artifacts]
