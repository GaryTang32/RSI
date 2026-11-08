"""Parallel variants: several experiments in flight on one branch, and independent chains."""

from __future__ import annotations

import hashlib
import time
import warnings
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ..core.run import ImprovementResult
from .executors import Executor, FakeSlurmExecutor, LocalProcessExecutor
from .loop import AutoresearchLoop


class ParallelAutoresearchLoop(AutoresearchLoop):
    """Up to ``workers`` experiments in flight, each judged against the incumbent when it completes."""

    def __init__(self, *args, pool: Executor | None = None, **kw) -> None:
        super().__init__(*args, **kw)
        w = max(1, self.cfg.workers)
        if pool is None:
            pool = (
                FakeSlurmExecutor(nodes=w, spool_dir=self.out / "slurm")
                if self.cfg.executor == "slurm"
                else LocalProcessExecutor(workers=w)
            )
        self.pool = pool
        self.inflight: dict[str, tuple] = {}
        self.completion_order: list[int] = []
        self._deferred: list[tuple] = []  # shadow audits postponed while runs are in flight
        self.n_deferred_audits = 0

    def _audit_competes(self) -> bool:
        """A shadow audit must not overlap in-flight runs, so its cost can be told apart from the loop's.

        ``_kept`` books the audit's ``usd()`` delta as monitor spend and credits its wall time back to ``max_wall_s``;
        with runs in flight that delta and that time include the runs' own LLM spend and progress, which would then
        escape ``max_usd`` / ``max_wall_s``. Under a wall-clock run budget the audit would also steal CPU from them.
        So audits wait until nothing is in flight (``_flush_audits``).
        """
        return self.monitor is not None and bool(self.inflight)

    def _kept(self, round: int, name: str, art, score) -> None:
        if self._audit_competes():
            self._deferred.append((round, name, art, score))
            self.n_deferred_audits += 1
            return
        super()._kept(round, name, art, score)

    def _flush_audits(self) -> None:
        while self._deferred and not self.inflight:
            super()._kept(*self._deferred.pop(0))

    def _submit_one(self) -> bool:
        nxt = self._next_candidate()
        if nxt is None:
            return False
        ctx, cand, desc, emeta = nxt
        self.n_experiments += 1
        name = f"exp{self.n_rounds:04d}"
        emeta["submitted_on"] = self.inc["sha"][:7]
        emeta["exp_index"] = self.n_rounds
        self.n_runs += 1
        jid = self.pool.submit(
            self.task,
            cand,
            seed=self._seeds(self.keep.repeats)[0],
            mode=self.cfg.mode,
            log_path=self._log_path(name),
            val_epoch=self.val_epoch,
            meta={"tag": name},
        )
        self.inflight[jid] = (ctx, cand, desc, emeta, name, self.n_rounds)
        return True

    def _collect(self) -> int:
        n = 0
        for jid in list(self.pool.done()):
            if jid not in self.inflight:
                continue
            ctx, cand, desc, emeta, name, rnd = self.inflight.pop(jid)
            outcome = self.pool.wait(jid)
            pseudo = hashlib.sha1(f"{cand.id}|{name}".encode()).hexdigest()
            emeta["slurm_state"] = self.pool.status(jid)
            self._trace_eval(name, outcome, f"experiment (parallel; proposed at exp {rnd}, judged at completion)")
            self._settle(ctx, cand, desc, pseudo, outcome, emeta, name, committed=False)
            self.completion_order.append(rnd)
            n += 1
        return n

    def context(self):
        ctx = super().context()
        if getattr(self, "inflight", None):
            running = "; ".join(v[2] for v in self.inflight.values())
            ctx.notes = (ctx.notes + "\n" if ctx.notes else "") + f"Currently running (do not duplicate): {running}"
            ctx.results_tsv += f"# in flight: {running}\n"
        return ctx

    def run(self) -> ImprovementResult:
        self.setup()
        self.baseline()
        stop = None
        while True:
            if stop is None:
                stop = self._stop_check()
            while stop is None and len(self.inflight) < max(1, self.cfg.workers):
                self._submit_one()
                stop = self._stop_check()
            if not self.inflight and stop is not None:
                break
            if self._collect() == 0:
                time.sleep(0.01)
            if not self.inflight:
                self._flush_audits()
        self._flush_audits()
        self.stop_reason = stop
        self.pool.shutdown()
        res = self.finish()
        par = res.meta["parallel"] = {
            "workers": self.cfg.workers,
            "executor": type(self.pool).__name__,
            "completion_order": self.completion_order,
            "deferred_audits": self.n_deferred_audits,
        }
        if isinstance(self.pool, FakeSlurmExecutor):
            par["slurm_states"] = sorted({j.state for j in self.pool.jobs.values()})
            par["slurm_warnings"] = list(self.pool.warnings)
        return res


class MultiChainLauncher:
    """N independent chains (upstream ``spawn.sh``); ``make_loop(i, out_dir)`` builds chain ``i``.

    Concurrent chains should each get their own LLM objects from ``make_loop``: a chain's spend is read from its
    LLMs' meters, so chains sharing an LLM would each count the others' spend.
    """

    def __init__(
        self,
        make_loop: Callable[[int, Path], AutoresearchLoop],
        n: int,
        out_dir: str | Path,
        tag: str = "run",
        concurrent: bool = True,
    ) -> None:
        self.make_loop = make_loop
        self.n = n
        self.out = Path(out_dir)
        self.tag = tag
        self.concurrent = concurrent

    @staticmethod
    def _check_shared_llms(loops: list[AutoresearchLoop]) -> None:
        """Concurrent chains that share an LLM cannot keep separate spend totals: refuse with a cap, else warn."""
        owners: dict[int, set[int]] = {}
        for i, lp in enumerate(loops):
            for llm in lp.llms:
                owners.setdefault(id(llm), set()).add(i)
        shared = sorted({i for s in owners.values() if len(s) > 1 for i in s})
        if not shared:
            return
        msg = (
            f"chains {shared} share LLM objects: each chain's spend is read from its LLMs' meters, so it would count "
            "the other chains' spend too; build separate LLMs per chain in make_loop(i, out_dir)"
        )
        if any(loops[i].cfg.max_usd is not None for i in shared):
            raise ValueError(f"{msg} (a per-chain max_usd cannot be enforced)")
        warnings.warn(f"{msg}; per-chain usage totals will overlap", stacklevel=3)

    def launch(self) -> list[ImprovementResult]:
        loops = []
        for i in range(self.n):
            lp = self.make_loop(i, self.out / f"w{i}")
            lp.cfg.tag = f"{self.tag}-w{i}"
            loops.append(lp)
        if not self.concurrent:
            return [lp.run() for lp in loops]
        self._check_shared_llms(loops)
        with ThreadPoolExecutor(self.n) as ex:
            return list(ex.map(lambda lp: lp.run(), loops))
