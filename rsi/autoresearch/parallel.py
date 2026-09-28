"""Parallel variants: the SkyPilot "don't wait" loop and spawn.sh's independent chains.

:class:`ParallelAutoresearchLoop` keeps up to ``workers`` experiments in flight
on an :class:`~rsi.autoresearch.executors.Executor` (local process pool or the
fake SLURM cluster). Each proposal is made from the incumbent at submission
time ("Don't wait - move on to the next idea"); when a run finishes it is judged
against the incumbent *at completion time* and, if kept, its files become the
new branch tip ("copy winning train.py back, commit"). Candidates are committed
only when kept. This turns greedy hill-climbing into a more grid-like search.

:class:`MultiChainLauncher` is the deleted upstream ``spawn.sh``: N independent
chains, one branch ``autoresearch/<tag>-w<i>`` and working area each, no sharing.
"""
from __future__ import annotations

import hashlib
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Optional

from ..core.run import ImprovementResult
from .executors import Executor, FakeSlurmExecutor, LocalProcessExecutor
from .loop import AutoresearchLoop


class ParallelAutoresearchLoop(AutoresearchLoop):
    """``workers`` experiments in flight; see module docstring."""

    def __init__(self, *args, pool: Optional[Executor] = None, **kw) -> None:
        super().__init__(*args, **kw)
        w = max(1, self.cfg.workers)
        if pool is None:
            pool = FakeSlurmExecutor(nodes=w, spool_dir=self.out / "slurm") if self.cfg.executor == "slurm" \
                else LocalProcessExecutor(workers=w)
        self.pool = pool
        self.inflight: dict[str, tuple] = {}
        self.completion_order: list[int] = []
        self._deferred: list[tuple] = []           # shadow audits postponed while runs were in flight
        self.n_deferred_audits = 0

    def _audit_competes(self) -> bool:
        """A shadow audit of a script task re-trains the incumbent. Under a wall-clock
        (or ceiling) budget that training would share the CPU with the in-flight runs and
        shorten their effective budget (validation AUDIT #23), so it waits."""
        return (self.monitor is not None and bool(self.inflight)
                and getattr(self.task.budget, "kind", "") in ("wallclock", "ceiling"))

    def _kept(self, round: int, name: str, art, score) -> None:
        if self._audit_competes():
            self._deferred.append((round, name, art, score))
            self.n_deferred_audits += 1
            return
        super()._kept(round, name, art, score)

    def _flush_audits(self) -> None:
        """Run the postponed audits (only called when nothing is in flight)."""
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
        jid = self.pool.submit(self.task, cand, seed=self._seeds(self.keep.repeats)[0], mode=self.cfg.mode,
                               log_path=self._log_path(name), val_epoch=self.val_epoch, meta={"tag": name})
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
                stop = self.budget.exhausted(rounds=self.n_rounds, rollouts=self.n_runs, usd=self.budget_usd())
                if stop is None and self._agent_failed():
                    stop = "agent_failed"
            while stop is None and len(self.inflight) < max(1, self.cfg.workers):
                self._submit_one()
                stop = self.budget.exhausted(rounds=self.n_rounds, rollouts=self.n_runs, usd=self.budget_usd())
                if stop is None and self._agent_failed():
                    stop = "agent_failed"
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
        res.meta["parallel"] = {"workers": self.cfg.workers, "executor": type(self.pool).__name__,
                                "completion_order": self.completion_order,
                                "deferred_audits": self.n_deferred_audits}
        if isinstance(self.pool, FakeSlurmExecutor):
            res.meta["parallel"]["slurm_states"] = sorted({j.state for j in self.pool.jobs.values()})
            res.meta["parallel"]["slurm_warnings"] = list(self.pool.warnings)
        return res


class MultiChainLauncher:
    """N independent greedy chains (upstream ``spawn.sh launch <tag> claude:0 claude:1 ...``).

    ``make_loop(i, out_dir) -> AutoresearchLoop`` builds worker ``i``'s loop (its own
    agent, keep rule and branch ``autoresearch/<tag>-w<i>``); chains run concurrently
    and never share state. Returns one ImprovementResult per chain.
    """

    def __init__(self, make_loop: Callable[[int, Path], AutoresearchLoop], n: int, out_dir: str | Path,
                 tag: str = "run", concurrent: bool = True) -> None:
        self.make_loop = make_loop
        self.n = n
        self.out = Path(out_dir)
        self.tag = tag
        self.concurrent = concurrent

    def launch(self) -> list[ImprovementResult]:
        loops = []
        for i in range(self.n):
            lp = self.make_loop(i, self.out / f"w{i}")
            lp.cfg.tag = f"{self.tag}-w{i}"
            loops.append(lp)
        if not self.concurrent:
            return [lp.run() for lp in loops]
        with ThreadPoolExecutor(self.n) as ex:
            return list(ex.map(lambda lp: lp.run(), loops))
