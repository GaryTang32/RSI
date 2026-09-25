"""Executors: where experiment runs happen (SLURM / SkyPilot / multi-GPU ports).

All executors share one small interface (``submit / status / wait / cancel /
fetch_log``) plus a synchronous ``run`` used by the sequential loop.

* :class:`SerialExecutor` - runs in the caller's thread (the default loop).
* :class:`LocalProcessExecutor` - a pool of ``workers`` concurrent runs; each run
  already gets its own working directory (no shared-root race).
* :class:`FakeSlurmExecutor` - mimics the SLURM forks: jobs are submitted with
  an sbatch script rendered from a template (optionally with pyxis-style
  ``--container-image`` lines), wait ``PENDING`` for a free node (plus an optional
  queue delay), run ``RUNNING`` under a job time limit, and end ``COMPLETED``,
  ``FAILED``, ``TIMEOUT``, ``OUT_OF_MEMORY`` or ``CANCELLED``. ``squeue`` returns an
  empty string once a job left the queue; ``sacct`` gives the final state; logs
  land in ``ret-<jobid>.out/.err``. :meth:`FakeSlurmExecutor.wait` follows the
  kcxain fork's rules: poll every ``poll_s``, report (not cancel) long PENDING,
  cancel and treat as crash when RUNNING exceeds ``running_limit_s``.
"""
from __future__ import annotations

import itertools
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..core.artifact import Artifact
from .task import RunOutcome

TERMINAL = ("COMPLETED", "FAILED", "TIMEOUT", "OUT_OF_MEMORY", "CANCELLED")


@dataclass
class Job:
    id: str
    artifact: Artifact
    seed: int
    mode: str
    meta: dict = field(default_factory=dict)
    state: str = "PENDING"
    submitted: float = field(default_factory=time.time)
    started: Optional[float] = None
    finished: Optional[float] = None
    outcome: Optional[RunOutcome] = None
    log: str = ""


def state_of(outcome: RunOutcome) -> str:
    """SLURM-style terminal state of a finished run."""
    if outcome.killed:
        return "TIMEOUT"
    if outcome.crashed:
        if "MemoryError" in (outcome.log or "") or "out of memory" in (outcome.crash_reason or "").lower():
            return "OUT_OF_MEMORY"
        return "FAILED"
    return "COMPLETED"


class Executor:
    name = "executor"

    def run(self, task, artifact: Artifact, *, seed: int = 0, mode: str = "hardened", log_path=None,
            val_epoch: int = 0) -> RunOutcome:
        jid = self.submit(task, artifact, seed=seed, mode=mode, log_path=log_path, val_epoch=val_epoch)
        return self.wait(jid)

    def submit(self, task, artifact, *, seed=0, mode="hardened", log_path=None, val_epoch=0, meta=None) -> str:
        raise NotImplementedError

    def status(self, job_id: str) -> str:
        raise NotImplementedError

    def wait(self, job_id: str, timeout: Optional[float] = None, poll_s: float = 0.05) -> RunOutcome:
        raise NotImplementedError

    def cancel(self, job_id: str) -> None:
        pass

    def fetch_log(self, job_id: str) -> str:
        return ""

    def done(self) -> list[str]:
        return []

    def shutdown(self) -> None:
        pass


class SerialExecutor(Executor):
    name = "serial"

    def __init__(self) -> None:
        self.jobs: dict[str, Job] = {}
        self._ids = itertools.count(1)

    def submit(self, task, artifact, *, seed=0, mode="hardened", log_path=None, val_epoch=0, meta=None) -> str:
        jid = str(next(self._ids))
        job = Job(jid, artifact, seed, mode, dict(meta or {}), state="RUNNING", started=time.time())
        job.outcome = task.run(artifact, seed=seed, mode=mode, log_path=log_path, val_epoch=val_epoch)
        job.state, job.finished, job.log = state_of(job.outcome), time.time(), job.outcome.log
        self.jobs[jid] = job
        return jid

    def status(self, job_id):
        return self.jobs[job_id].state

    def wait(self, job_id, timeout=None, poll_s=0.05):
        return self.jobs[job_id].outcome

    def fetch_log(self, job_id):
        return self.jobs[job_id].log

    def done(self):
        return [j for j, job in self.jobs.items() if job.state in TERMINAL]


class LocalProcessExecutor(Executor):
    """``workers`` concurrent runs (threads driving subprocess/in-process runs)."""

    name = "local"

    def __init__(self, workers: int = 4) -> None:
        self.workers = workers
        self.pool = ThreadPoolExecutor(max_workers=workers)
        self.jobs: dict[str, Job] = {}
        self.futures: dict[str, Future] = {}
        self._ids = itertools.count(1)
        self._lock = threading.Lock()

    def submit(self, task, artifact, *, seed=0, mode="hardened", log_path=None, val_epoch=0, meta=None) -> str:
        with self._lock:
            jid = str(next(self._ids))
            job = Job(jid, artifact, seed, mode, dict(meta or {}))
            self.jobs[jid] = job

        def work():
            job.state, job.started = "RUNNING", time.time()
            out = task.run(artifact, seed=seed, mode=mode, log_path=log_path, val_epoch=val_epoch)
            job.outcome, job.log, job.finished = out, out.log, time.time()
            job.state = state_of(out) if job.state != "CANCELLED" else "CANCELLED"
            return out

        self.futures[jid] = self.pool.submit(work)
        return jid

    def status(self, job_id):
        return self.jobs[job_id].state

    def wait(self, job_id, timeout=None, poll_s=0.05):
        return self.futures[job_id].result(timeout=timeout)

    def cancel(self, job_id):
        f = self.futures.get(job_id)
        if f is not None and f.cancel():
            self.jobs[job_id].state = "CANCELLED"

    def fetch_log(self, job_id):
        return self.jobs[job_id].log

    def done(self):
        return [j for j, f in self.futures.items() if f.done()]

    def shutdown(self):
        self.pool.shutdown(wait=True)


SBATCH_TEMPLATE = """#!/bin/bash
#SBATCH --job-name=ar_{tag}
#SBATCH --partition={partition}
#SBATCH --gres={gres}
#SBATCH --time={time_limit}
#SBATCH --output=ret-%j.out
#SBATCH --error=ret-%j.err
{container_lines}
cd {workdir} && {run_cmd}
"""


class FakeSlurmExecutor(Executor):
    """A local stand-in for a SLURM cluster (see module docstring)."""

    name = "fake-slurm"

    def __init__(self, nodes: int = 2, *, spool_dir: Optional[str | Path] = None, queue_delay_s: float = 0.0,
                 time_limit_s: Optional[float] = None, partition: str = "cpu", gres: str = "gpu:1",
                 container_image: Optional[str] = None, container_mounts: Optional[str] = None,
                 poll_s: float = 0.05, pending_limit_s: float = 600.0, running_limit_s: Optional[float] = None) -> None:
        import tempfile

        self.nodes = nodes
        self.spool = Path(spool_dir or tempfile.mkdtemp(prefix="rsi_fakeslurm_"))
        self.spool.mkdir(parents=True, exist_ok=True)
        self.queue_delay_s = queue_delay_s
        self.time_limit_s = time_limit_s
        self.partition, self.gres = partition, gres
        self.container_image, self.container_mounts = container_image, container_mounts
        self.poll_s = poll_s
        self.pending_limit_s = pending_limit_s
        self.running_limit_s = running_limit_s
        self.jobs: dict[str, Job] = {}
        self.scripts: dict[str, str] = {}
        self._payload: dict[str, tuple] = {}
        self._ids = itertools.count(1000)
        self._lock = threading.Lock()
        self._free = threading.Semaphore(nodes)
        self.events: list[tuple[float, str, str]] = []    # (t, job, state) transitions
        self.warnings: list[str] = []
        self._stop = False
        self._dispatcher = threading.Thread(target=self._dispatch, daemon=True)
        self._dispatcher.start()

    # ---- the "cluster"
    def render_sbatch(self, job_id: str, task, tag: str) -> str:
        lines = ""
        if self.container_image:
            lines = f"#SBATCH --container-image={self.container_image}\n"
            if self.container_mounts:
                lines += f"#SBATCH --container-mounts={self.container_mounts}\n"
            lines += "#SBATCH --container-writable\n"
        tl = self.time_limit_s or task.budget.hard_timeout()
        return SBATCH_TEMPLATE.format(tag=tag, partition=self.partition, gres=self.gres,
                                      time_limit=time.strftime("%H:%M:%S", time.gmtime(tl)),
                                      container_lines=lines, workdir=str(self.spool / f"exp_{job_id}"),
                                      run_cmd=getattr(task, "run_command", "python train.py"))

    def _event(self, jid: str, state: str) -> None:
        with self._lock:
            self.jobs[jid].state = state
            self.events.append((time.time(), jid, state))

    def _dispatch(self) -> None:
        while not self._stop:
            pending = None
            with self._lock:
                for jid, job in self.jobs.items():
                    if job.state == "PENDING" and time.time() - job.submitted >= self.queue_delay_s:
                        pending = jid
                        break
            if pending is None or not self._free.acquire(timeout=self.poll_s):
                time.sleep(self.poll_s / 2)
                continue
            with self._lock:
                if self.jobs[pending].state != "PENDING":
                    self._free.release()
                    continue
            threading.Thread(target=self._execute, args=(pending,), daemon=True).start()

    def _execute(self, jid: str) -> None:
        task, log_path, val_epoch = self._payload[jid]
        job = self.jobs[jid]
        job.started = time.time()
        self._event(jid, "RUNNING")
        try:
            out = task.run(job.artifact, seed=job.seed, mode=job.mode, log_path=log_path, val_epoch=val_epoch)
            if self.time_limit_s is not None and out.wall_s > self.time_limit_s and not out.killed:
                out.killed, out.metric = True, None
                out.crash_reason = f"killed: exceeded the job time limit {self.time_limit_s:.0f}s"
        except Exception as e:  # noqa: BLE001 - a node failure is a job failure
            out = RunOutcome(None, log=repr(e), crash_reason=f"NODE_FAIL: {e!r}")
        job.outcome, job.log, job.finished = out, out.log, time.time()
        (self.spool / f"ret-{jid}.out").write_text(out.log)
        (self.spool / f"ret-{jid}.err").write_text(out.crash_reason or "")
        if job.state != "CANCELLED":
            self._event(jid, state_of(out))
        self._free.release()

    # ---- the CLI
    def sbatch(self, script: str, task, artifact: Artifact, *, seed=0, mode="hardened", log_path=None, val_epoch=0,
               meta=None, job_id: Optional[str] = None) -> str:
        """``sbatch --parsable script`` -> job id."""
        with self._lock:
            jid = job_id or str(next(self._ids))
            self.jobs[jid] = Job(jid, artifact, seed, mode, dict(meta or {}))
            self.scripts[jid] = script
            self._payload[jid] = (task, log_path, val_epoch)
            self.events.append((time.time(), jid, "PENDING"))
        (self.spool / f"ar_{jid}.sbatch").write_text(script)
        return jid

    def squeue(self, job_id: str) -> str:
        """``squeue -j <id> -h -o %T``: PENDING / RUNNING, or '' once the job left the queue."""
        st = self.jobs[job_id].state
        return st if st in ("PENDING", "RUNNING") else ""

    def sacct(self, job_id: str) -> str:
        return self.jobs[job_id].state

    def scancel(self, job_id: str) -> None:
        job = self.jobs[job_id]
        if job.state in ("PENDING", "RUNNING"):
            self._event(job_id, "CANCELLED")

    # ---- Executor interface
    def submit(self, task, artifact, *, seed=0, mode="hardened", log_path=None, val_epoch=0, meta=None) -> str:
        with self._lock:
            jid = str(next(self._ids))
        script = self.render_sbatch(jid, task, (meta or {}).get("tag", "exp"))
        return self.sbatch(script, task, artifact, seed=seed, mode=mode, log_path=log_path, val_epoch=val_epoch,
                           meta=meta, job_id=jid)

    def status(self, job_id):
        return self.jobs[job_id].state

    def wait(self, job_id, timeout=None, poll_s=None):
        poll = poll_s or self.poll_s
        t0 = time.time()
        warned = False
        while True:
            q = self.squeue(job_id)
            job = self.jobs[job_id]
            if q == "":
                break
            if q == "PENDING" and time.time() - job.submitted > self.pending_limit_s and not warned:
                self.warnings.append(f"job {job_id} PENDING for more than {self.pending_limit_s:.0f}s; waiting")
                warned = True
            if q == "RUNNING" and self.running_limit_s is not None and job.started and \
                    time.time() - job.started > self.running_limit_s:
                self.scancel(job_id)
                return RunOutcome(None, log="", killed=True, crash_reason=
                                  f"killed: RUNNING longer than {self.running_limit_s:.0f}s (scancel)")
            if timeout is not None and time.time() - t0 > timeout:
                self.scancel(job_id)
                return RunOutcome(None, log="", killed=True, crash_reason="killed: wait timeout (scancel)")
            time.sleep(poll)
        state = self.sacct(job_id)
        out = self.jobs[job_id].outcome
        if state == "CANCELLED":
            return RunOutcome(None, log=out.log if out else "", crash_reason="CANCELLED")
        while out is None:            # terminal state set just before outcome stored
            time.sleep(0.001)
            out = self.jobs[job_id].outcome
        if state == "TIMEOUT" and out.metric is not None:
            out.metric = None
            out.crash_reason = "TIMEOUT: the training script hung"
        return out

    def cancel(self, job_id):
        self.scancel(job_id)

    def fetch_log(self, job_id):
        p = self.spool / f"ret-{job_id}.out"
        return p.read_text() if p.exists() else ""

    def done(self):
        return [j for j, job in self.jobs.items() if job.state in TERMINAL and job.outcome is not None]

    def shutdown(self):
        self._stop = True
