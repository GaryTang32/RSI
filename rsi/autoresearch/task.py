"""Research tasks: the only domain-specific part of an autoresearch loop.

A :class:`ResearchTask` is upstream's ``prepare.py`` contract made explicit:

* which files the agent may edit (``editable_paths``, upstream ``["train.py"]``)
  and which are locked (``locked_paths``, upstream ``["prepare.py"]``);
* a fixed per-experiment budget (:class:`RunBudget`, upstream "5 minutes of
  training, kill after 10") that the agent cannot change;
* how one experiment is run and how its one number is read
  (:meth:`ResearchTask.run` -> :class:`RunOutcome`, summary lines ``key: value``
  parsed by :func:`parse_summary`; no metric line means the run crashed);
* optional hidden audits (splits the agent never sees, scored post hoc).

Two generic implementations cover most uses:

:class:`ScriptResearchTask`  a directory of files run as a subprocess
    (``python train.py``) inside a per-experiment working directory, with the
    wall-clock kill enforced by :func:`rsi.core.sandbox.run_cmd`. The tinylm and
    tabular domains are script tasks.
:class:`DomainResearchTask`  any :class:`rsi.core.Domain` + artifact: one
    experiment = one fixed-size evaluation on the decision split; hidden audits
    use the sealed splits. This is how the loop applies to harnesses, prompts or
    any other problem already expressed as a Domain.

``mode`` selects upstream semantics (``"faithful"``: locked files protected only
by instructions, metric read from the log) or the mechanically locked variant
(``"hardened"``: locked files restored from a sealed copy and hash-checked,
metric taken from a framework-owned record written by locked code). See
``rsi.autoresearch.guard``.
"""
from __future__ import annotations

import json
import math
import re
import shutil
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from ..core.artifact import Artifact
from ..core.domain import Domain
from ..core.evaluate import Evaluator
from ..core.llm import LLM
from ..core.sandbox import run_cmd

MODES = ("faithful", "hardened")
_LINE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*):\s+(\S.*?)\s*$", re.M)


@dataclass
class RunBudget:
    """The fixed per-experiment budget (never editable by the agent).

    ``kind``: ``"wallclock"`` (seconds of training, upstream), ``"tokens"``
    (training bytes/tokens served by the locked loader - deterministic ablation),
    ``"ceiling"`` (a wall-time cap on the whole run, xgboost port: runs over it are
    discarded), ``"rollouts"`` (a fixed evaluation size, Domain tasks) or ``"none"``.
    ``kill_after``: hard kill of the whole run (upstream 10 min for a 5 min budget);
    defaults to ``2 * amount + slack_s``.
    """

    kind: str = "wallclock"
    amount: float = 25.0
    warmup_excluded_steps: int = 11
    kill_after: Optional[float] = None
    slack_s: float = 5.0
    mem_mb: Optional[int] = 4096

    def hard_timeout(self) -> float:
        if self.kill_after is not None:
            return float(self.kill_after)
        if self.kind in ("wallclock", "ceiling"):
            return 2.0 * self.amount + self.slack_s
        return 600.0

    def to_json(self) -> dict:
        return {"kind": self.kind, "amount": self.amount, "warmup_excluded_steps": self.warmup_excluded_steps,
                "kill_after": self.hard_timeout(), "mem_mb": self.mem_mb}


@dataclass
class RunOutcome:
    """What one experiment run produced. ``metric is None`` means crash."""

    metric: Optional[float]
    log: str = ""
    summary: Optional[dict] = None
    memory_gb: float = 0.0
    wall_s: float = 0.0
    returncode: int = 0
    killed: bool = False
    crash_reason: Optional[str] = None
    over_budget: bool = False
    record: dict = field(default_factory=dict)      # framework-owned result record (hardened)
    log_path: Optional[str] = None
    seed: int = 0
    meta: dict = field(default_factory=dict)

    @property
    def crashed(self) -> bool:
        return self.metric is None

    def tail(self, n: int = 50) -> str:
        """``tail -n 50 run.log`` - the only part of a failed log the agent reads."""
        return "\n".join(self.log.strip().splitlines()[-n:])

    def to_json(self) -> dict:
        return {"metric": self.metric, "memory_gb": self.memory_gb, "wall_s": round(self.wall_s, 3),
                "returncode": self.returncode, "killed": self.killed, "crash_reason": self.crash_reason,
                "over_budget": self.over_budget, "summary": self.summary, "record": self.record, "seed": self.seed,
                "log_path": self.log_path}


def parse_summary(log: str, required: Optional[str] = None) -> Optional[dict]:
    """Parse ``key: value`` summary lines (upstream ``grep "^val_bpb:" run.log``).
    Returns None when ``required`` is given but absent (the crash signal)."""
    out: dict[str, Any] = {}
    for k, v in _LINE.findall(log or ""):
        try:
            out[k] = float(v)
        except ValueError:
            out[k] = v
    if required is not None and not isinstance(out.get(required), float):
        return None
    return out


RECORD_HELPER = '''
def report(**metrics):
    """Print the summary block and, when the autoresearch framework asks for it, write the
    framework-owned result record (hardened mode reads the metric from here, not the log)."""
    import json, os
    print("---")
    for k, v in metrics.items():
        print(f"{k}: {v}")
    path = os.environ.get("RSI_AR_RESULT")
    if path and os.environ.get("RSI_AR_MODE", "faithful") != "faithful":
        with open(path, "w") as f:
            json.dump(metrics, f)
'''
"""Paste into your *locked* evaluation file and call ``report(metric=value, ...)`` from it,
so a :class:`ScriptResearchTask` can run your problem in hardened mode."""


class ResearchTask:
    """Base class. Subclasses implement :meth:`run` (and usually :meth:`seed_artifact`)."""

    name: str = "task"
    metric: str = "score"
    direction: str = "max"                         # "min" (val_bpb) | "max" (AUC, accuracy)
    editable_paths: tuple[str, ...] = ("**",)
    locked_paths: tuple[str, ...] = ()
    memory_key: str = "peak_mem_mb"
    audit_splits: tuple[str, ...] = ()
    run_command: str = ""
    budget: RunBudget = RunBudget()
    #: hardened mode: regexes that added lines of editable files must not match
    #: (see :func:`rsi.autoresearch.guard.default_tamper_patterns`)
    tamper_patterns: tuple[str, ...] = ()

    # ---- to implement
    def seed_artifact(self) -> Artifact:
        raise NotImplementedError

    def run(self, artifact: Artifact, *, seed: int = 0, mode: str = "hardened", log_path: Optional[str] = None,
            val_epoch: int = 0) -> RunOutcome:
        raise NotImplementedError

    # ---- optional hooks
    def prepare(self) -> None:
        """One-time, idempotent data preparation (``uv run prepare.py``)."""

    def audit(self, artifact: Artifact, *, seed: int = 0) -> dict[str, float]:
        """Hidden metrics of ``artifact`` (post hoc, never shown to the agent). ``{}`` means
        no audit is configured; a failed audit returns ``{"audit_error": reason}``."""
        return {}

    def sealed_files(self) -> dict[str, str]:
        """Canonical content of the locked files (hardened mode restores these)."""
        return {}

    def describe(self) -> str:
        return f"Research task {self.name}: optimise {self.metric} ({'lower' if self.direction == 'min' else 'higher'} is better)."

    def contract(self, mode: str) -> str:
        """Interface rules the editable code must follow (rendered into program.md)."""
        return ""

    def mock_edit_pool(self) -> list:
        """Scripted edits for :class:`~rsi.autoresearch.agent.MockResearchAgent`."""
        return []

    def truth(self, artifact: Artifact) -> Optional[float]:
        """Noise-free quality if the task knows it (synthetic tasks only)."""
        return None

    # ---- helpers
    def better(self, a: float, b: float) -> bool:
        """True iff metric value ``a`` is strictly better than ``b``."""
        return a < b if self.direction == "min" else a > b

    def gain(self, cand: float, inc: float) -> float:
        """Improvement of ``cand`` over ``inc`` (positive = better)."""
        return inc - cand if self.direction == "min" else cand - inc

    def memory_gb(self, summary: Optional[dict], record: Optional[dict] = None) -> float:
        mb = (record or {}).get(self.memory_key)
        if mb is None and summary:
            mb = summary.get(self.memory_key)
        try:
            return round(float(mb) / 1024.0, 1) if mb is not None else 0.0
        except (TypeError, ValueError):
            return 0.0


# --------------------------------------------------------------------------- script tasks
class ScriptResearchTask(ResearchTask):
    """A task made of files run as a subprocess in a fresh working directory.

    Parameters
    ----------
    files:
        the repository (locked + editable files), e.g. ``{"prepare.py": ..., "train.py": ...}``.
    run_cmd:
        command run in the working directory; ``"python"`` means this interpreter.
    data_dirs:
        ``{"faithful": dir, "hardened": dir, "audit": dir}`` passed to the scripts
        as ``RSI_AR_DATA`` (hardened runs should not see hidden audit data).
    record_checks:
        hardened-mode validation of the locked result record: callables
        ``record -> Optional[str]`` returning a crash reason.
    tamper_patterns:
        extra hardened-mode denylist regexes for added lines of editable files
        (e.g. names of hidden splits or of the validation arrays), on top of
        :func:`~rsi.autoresearch.guard.default_tamper_patterns` (framework
        environment variables; private names of, and assignments to, the locked
        modules) unless ``default_tamper=False``.
    """

    def __init__(
        self,
        name: str,
        files: Mapping[str, str],
        *,
        metric: str,
        direction: str,
        editable_paths: Sequence[str] = ("train.py",),
        locked_paths: Sequence[str] = ("prepare.py",),
        run_cmd: Sequence[str] = ("python", "train.py"),
        budget: Optional[RunBudget] = None,
        memory_key: str = "peak_mem_mb",
        data_dirs: Optional[Mapping[str, str]] = None,
        env: Optional[Mapping[str, str]] = None,
        description: str = "",
        record_checks: Sequence[Callable[[dict], Optional[str]]] = (),
        keep_workdirs: bool = False,
        tamper_patterns: Sequence[str] = (),
        default_tamper: bool = True,
    ) -> None:
        from .guard import default_tamper_patterns

        self.name = name
        self._files = dict(files)
        self.metric = metric
        self.direction = direction
        self.editable_paths = tuple(editable_paths)
        self.locked_paths = tuple(locked_paths)
        self.run_cmd = tuple(run_cmd)
        self.run_command = " ".join(self.run_cmd)
        self.budget = budget or RunBudget()
        self.memory_key = memory_key
        self.data_dirs = dict(data_dirs or {})
        self.env = dict(env or {})
        self._description = description
        self.record_checks = list(record_checks)
        self.keep_workdirs = keep_workdirs
        self.tamper_patterns = tuple((default_tamper_patterns(self.locked_paths) if default_tamper else [])
                                     + list(tamper_patterns))

    def seed_artifact(self) -> Artifact:
        return Artifact(self._files)

    def sealed_files(self) -> dict[str, str]:
        from .guard import matches

        return {k: v for k, v in self._files.items() if matches(k, self.locked_paths)}

    def describe(self) -> str:
        return self._description or super().describe()

    def _env(self, mode: str, seed: int, result_file: str, val_epoch: int) -> dict[str, str]:
        env = {"RSI_AR_MODE": mode, "RSI_AR_RUN_SEED": str(int(seed)), "RSI_AR_BUDGET_KIND": self.budget.kind,
               "RSI_AR_BUDGET": repr(float(self.budget.amount)), "RSI_AR_VAL_EPOCH": str(int(val_epoch))}
        dd = self.data_dirs.get(mode) or self.data_dirs.get("faithful")
        if dd:
            env["RSI_AR_DATA"] = str(dd)
        if mode != "faithful":
            env["RSI_AR_RESULT"] = result_file
        env.update(self.env)
        return env

    def run(self, artifact: Artifact, *, seed: int = 0, mode: str = "hardened", log_path: Optional[str] = None,
            val_epoch: int = 0) -> RunOutcome:
        from .guard import ScopeGuard

        work = Path(tempfile.mkdtemp(prefix=f"rsi_ar_{self.name}_"))
        side = Path(tempfile.mkdtemp(prefix="rsi_ar_rec_"))
        guard = ScopeGuard(self.editable_paths, self.locked_paths, sealed=self.sealed_files())
        try:
            files = dict(artifact.files)
            if mode != "faithful":
                files.update(guard.sealed)                      # restore locked files from the sealed store
            Artifact(files).to_dir(work)
            if mode != "faithful":
                guard.lock_dir(work)
                bad = guard.locked_hashes_ok(work)
                if bad:
                    return RunOutcome(None, log="", crash_reason=f"locked files differ from sealed copy: {bad}")
            result_file = str(side / "result.json")
            cmd = [sys.executable if c == "python" else c for c in self.run_cmd]
            rr = run_cmd(cmd, cwd=work, timeout_s=self.budget.hard_timeout(), mem_mb=self.budget.mem_mb,
                         env=self._env(mode if mode != "audit" else "audit", seed, result_file, val_epoch))
            log = rr.stdout + (("\n" + rr.stderr) if rr.stderr.strip() else "")
            if log_path:
                Path(log_path).parent.mkdir(parents=True, exist_ok=True)
                Path(log_path).write_text(log)
            out = RunOutcome(None, log=log, wall_s=rr.wall_s, returncode=rr.returncode, killed=rr.timed_out,
                             log_path=log_path, seed=seed)
            out.summary = parse_summary(log)
            if mode != "faithful":
                tampered = guard.locked_hashes_ok(work)
                if tampered:
                    out.crash_reason = f"locked files modified during the run: {tampered}"
                    return out
                try:
                    out.record = json.loads(Path(result_file).read_text())
                except (OSError, json.JSONDecodeError):
                    out.record = {}
            self._finish(out, mode)
            return out
        finally:
            if not self.keep_workdirs:
                shutil.rmtree(work, ignore_errors=True)
            shutil.rmtree(side, ignore_errors=True)

    def _finish(self, out: RunOutcome, mode: str) -> None:
        if out.killed:
            out.crash_reason = f"killed: exceeded kill_after={self.budget.hard_timeout():.0f}s"
            return
        if out.returncode != 0:
            if re.search(r"^FAIL\s*$", out.log, re.M):
                out.crash_reason = "FAIL (fast-fail: loss is NaN or exploded)"
            else:
                lines = [l for l in out.log.strip().splitlines() if l.strip() and not l.startswith(" ")]
                out.crash_reason = (lines[-1] if lines else f"exit code {out.returncode}")[:300]
            return
        if mode == "faithful":
            v = (out.summary or {}).get(self.metric)
        else:
            v = out.record.get(self.metric)
            if v is None:
                out.crash_reason = "no locked result record (the locked evaluator did not run)"
                return
            flags = [r for r in (check(out.record) for check in self.record_checks) if r]
            if flags and mode != "audit":
                out.crash_reason = flags[0]
                return
            if flags:                      # audit: score it anyway and report the protocol violations
                out.meta["audit_flags"] = flags
        if not isinstance(v, (int, float)) or not math.isfinite(float(v)):
            out.crash_reason = out.crash_reason or (
                "FAIL (fast-fail: loss is NaN or exploded)" if re.search(r"^FAIL\s*$", out.log, re.M)
                else f"no '{self.metric}:' line in the log")
            return
        out.metric = float(v)
        out.memory_gb = self.memory_gb(out.summary, out.record)
        if self.budget.kind == "ceiling" and out.wall_s > self.budget.amount:
            out.over_budget = True

    def audit(self, artifact: Artifact, *, seed: int = 0) -> dict[str, float]:
        """Re-run ``artifact`` in audit mode (locked files restored, hidden data
        visible to the locked evaluator only) and return the hidden metrics. A
        version that fails a ``record_checks`` rule is still scored; the failures
        come back as ``audit_flags`` (a crashed run gives ``audit_error``)."""
        if "audit" not in self.data_dirs:
            return {}
        out = self.run(artifact, seed=seed, mode="audit")
        if out.crashed:
            return {"audit_error": out.crash_reason or "crash"}
        aud = dict(out.record.get("audit") or {})
        aud["loop_metric_rerun"] = out.metric
        if out.meta.get("audit_flags"):
            aud["audit_flags"] = "; ".join(out.meta["audit_flags"])
        return aud


# --------------------------------------------------------------------------- Domain adapter
class DomainResearchTask(ResearchTask):
    """Apply the autoresearch loop to any :class:`rsi.core.Domain`.

    One experiment = evaluating the artifact on ``split`` with ``k`` trials per
    task (a fixed evaluation size: the budget). The metric is the domain score
    ``S`` (higher is better); ``cost`` (tokens per trial) is reported as an extra
    summary line. A run whose trials all error is a crash. Hidden audits are the
    sealed splits (``holdout``/``ood``), unsealed only by :meth:`audit`.

    ``kill_after`` is a *soft* limit here (the evaluation runs in a thread that
    cannot be killed); use a :class:`ScriptResearchTask` for hard kills.
    """

    def __init__(
        self,
        domain: Domain,
        llm: Optional[LLM] = None,
        *,
        seed_artifact: Optional[Artifact] = None,
        split: str = "evolve",
        k: int = 1,
        workers: int = 4,
        editable_paths: Sequence[str] = ("**",),
        locked_paths: Sequence[str] = (),
        kill_after: float = 1800.0,
        audit_splits: Sequence[str] = ("holdout", "ood"),
        crash_error_rate: float = 1.0,
        name: Optional[str] = None,
    ) -> None:
        self.domain = domain
        self.llm = llm
        self._seed = seed_artifact
        self.split = split
        self.k = k
        self.workers = workers
        self.editable_paths = tuple(editable_paths)
        self.locked_paths = tuple(locked_paths)
        self.name = name or f"domain:{domain.name}"
        self.metric = "score"
        self.direction = "max"
        self.memory_key = "peak_mem_mb"
        self.audit_splits = tuple(s for s in audit_splits if s in domain.tasks.splits)
        self.crash_error_rate = crash_error_rate
        n = len(domain.tasks.splits.get(split, []))
        self.budget = RunBudget(kind="rollouts", amount=float(n * k), kill_after=kill_after, mem_mb=None)
        self.run_command = f"evaluate(artifact, split={split!r}, k={k})"

    def seed_artifact(self) -> Artifact:
        if self._seed is None:
            seed_fn = getattr(self.domain, "seed_artifact", None)
            if seed_fn is None:
                raise ValueError("pass seed_artifact= for this domain")
            self._seed = seed_fn()
        return self._seed

    def describe(self) -> str:
        return self.domain.describe()

    def _evaluate(self, artifact: Artifact, split: str, seed: int, allow_sealed: bool = False):
        ev = Evaluator(self.domain, self.llm, workers=self.workers, allow_sealed=allow_sealed,
                       seed_offset=int(seed) * 1000)
        box: dict = {}

        def work():
            try:
                box["res"] = ev.evaluate(artifact, split, self.k)
            except Exception as e:  # noqa: BLE001 - a broken artifact is a crash, not a loop failure
                import traceback

                box["err"] = f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=5)}"

        th = threading.Thread(target=work, daemon=True)
        th.start()
        th.join(self.budget.hard_timeout())
        if th.is_alive():
            return None, f"killed: evaluation exceeded kill_after={self.budget.hard_timeout():.0f}s"
        return box.get("res"), box.get("err")

    def run(self, artifact: Artifact, *, seed: int = 0, mode: str = "hardened", log_path: Optional[str] = None,
            val_epoch: int = 0) -> RunOutcome:
        t0 = time.time()
        res, err = self._evaluate(artifact, self.split, seed + 7919 * val_epoch)
        wall = time.time() - t0
        if res is None:
            out = RunOutcome(None, log=err or "", wall_s=wall, killed=bool(err and err.startswith("killed")),
                             crash_reason=(err or "").splitlines()[0] if err else "evaluation failed", seed=seed)
        else:
            lines = []
            errs = [t for trs in res.trials.values() for t in trs if t.error]
            for t in errs[:3]:
                lines.append(f"[trial {t.task_id} seed {t.seed}] error: {t.error}\n{(t.trace or '')[-800:]}")
            lines += ["---", f"score:            {res.score:.6f}", f"cost_tokens:      {res.cost:.1f}",
                      f"steps:            {res.steps:.2f}", f"error_rate:       {res.error_rate:.3f}",
                      f"n_trials:         {res.n_trials}", f"training_seconds: {wall:.1f}",
                      f"peak_mem_mb:      0.0"]
            log = "\n".join(lines) + "\n"
            out = RunOutcome(None, log=log, summary=parse_summary(log), wall_s=wall, seed=seed,
                             meta={"cost": res.cost, "error_rate": res.error_rate, "per_task": res.task_scores()})
            if res.error_rate >= self.crash_error_rate and res.n_trials:
                first = errs[0].error if errs else "?"
                out.crash_reason = f"every trial errored: {first}"
                out.log += f"{first}\n"                     # the traceback's last line, as in run.log
            else:
                out.metric = float(res.score)
        if log_path:
            Path(log_path).parent.mkdir(parents=True, exist_ok=True)
            Path(log_path).write_text(out.log)
            out.log_path = log_path
        return out

    def audit(self, artifact: Artifact, *, seed: int = 0) -> dict[str, float]:
        out: dict = {}
        for split in self.audit_splits:
            res, err = self._evaluate(artifact, split, seed, allow_sealed=True)
            if res is not None:
                out[split] = float(res.score)
            else:
                out["audit_error"] = f"{split}: {(err or 'evaluation failed').splitlines()[0]}"
        return out
