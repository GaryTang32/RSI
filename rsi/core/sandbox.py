"""Run untrusted, machine-written code safely-ish.

Self-improving loops execute code an LLM just wrote (training scripts, solvers,
harnesses, search policies, validation commands). :func:`run_python` runs it in a
separate process with a wall-clock timeout, CPU/memory rlimits, a scratch working
directory and a scrubbed environment. This is isolation against accidents
(infinite loops, OOM, stray writes), not a security boundary against a
determined adversary - use containers for that.
"""
from __future__ import annotations

import dataclasses
import json
import math
import os
import resource
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, Sequence

from .artifact import is_safe_relpath


@dataclass
class RunResult:
    returncode: int
    stdout: str
    stderr: str
    wall_s: float
    timed_out: bool

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out

    def tail(self, n: int = 50) -> str:
        return "\n".join((self.stdout + "\n" + self.stderr).strip().splitlines()[-n:])


def _limits(cpu_s: Optional[int], mem_mb: Optional[int]):
    """Legacy ``preexec_fn`` form of the limits (kept for callers that import it;
    :func:`run_cmd` now uses :func:`_apply_limits`, which is safe with threads)."""
    def apply():
        os.setsid()
        if cpu_s:
            resource.setrlimit(resource.RLIMIT_CPU, (cpu_s, cpu_s + 5))
        if mem_mb:
            b = mem_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (b, b))
    return apply


def _apply_limits(pid: int, cpu_s: Optional[int], mem_mb: Optional[int]) -> None:
    """Set CPU / address-space rlimits on a just-started child with ``prlimit``.

    Replaces a ``preexec_fn``, which Python documents as unsafe when the parent
    has threads (the Evaluator's workers reach this through tool calls): the
    child could deadlock between fork and exec. The child starts a few
    microseconds before the limits apply, far less than an interpreter start."""
    # each limit on its own: one that cannot be set (the child already exited, or a hard limit this process may not
    # raise) never drops the other
    if cpu_s:
        # the hard limit 5 s above the soft one, but never above a hard limit this process may not raise
        hard = resource.getrlimit(resource.RLIMIT_CPU)[1]
        hard = cpu_s + 5 if hard == resource.RLIM_INFINITY else min(cpu_s + 5, hard)
        try:
            resource.prlimit(pid, resource.RLIMIT_CPU, (min(cpu_s, hard), hard))
        except (ProcessLookupError, PermissionError, ValueError):
            pass
    if mem_mb:
        b = mem_mb * 1024 * 1024
        try:
            resource.prlimit(pid, resource.RLIMIT_AS, (b, b))
        except (ProcessLookupError, PermissionError, ValueError):
            pass


#: (pid of the process that started it, process group) for every sandboxed child still running
_LIVE_GROUPS: set[tuple[int, int]] = set()
#: (pid of the process that made it, path) for every scratch directory still in use
_LIVE_DIRS: set[tuple[int, str]] = set()
#: processes that called close_sandboxes(): they start no new sandbox
_CLOSED: set[int] = set()
_LIVE_LOCK = threading.Lock()


def _fresh_lock_after_fork() -> None:
    """A forked child gets a new lock: another thread may have held the parent's at the fork, and nothing in the
    child would ever release it."""
    global _LIVE_LOCK
    _LIVE_LOCK = threading.Lock()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_fresh_lock_after_fork)


def close_sandboxes() -> int:
    """For a process about to exit (e.g. a forked child): start no new sandbox, kill every sandboxed process group
    this process started that is still running, whichever thread started it, and remove this process's scratch
    directories. A spawn in progress registers under the same lock, so none slips through. Groups and directories a
    parent made before a fork are left alone. Returns how many groups it killed; safe to call again."""
    me = os.getpid()
    with _LIVE_LOCK:
        _CLOSED.add(me)
        groups = {g for owner, g in _LIVE_GROUPS if owner == me}
        dirs = [d for owner, d in _LIVE_DIRS if owner == me]
    for g in groups:
        _killpg(g)
    for d in dirs:
        shutil.rmtree(d, ignore_errors=True)
    # every sandbox leads its own session as a direct child: also catch one an interrupted run_cmd never registered
    # (or deregistered before killing it); after the registered ones, so a failing scan can never stop those
    extra = kill_child_sessions(me) - groups
    return len(groups) + len(extra)


def _killpg(g: int) -> None:
    try:
        os.killpg(g, 9)
    except (ProcessLookupError, PermissionError):
        pass


def kill_child_sessions(pid: int) -> set[int]:
    """Kill the process group of every direct child of ``pid`` that leads its own (every sandbox does): the
    sandboxes of a process that cannot close them itself, e.g. a forked child about to be SIGKILLed. Returns the
    groups signalled."""
    groups = _child_session_leaders(pid)
    for g in groups:
        _killpg(g)
    return groups


def _child_session_leaders(pid: int) -> set[int]:
    """Direct children of ``pid`` that lead their own process group (Linux /proc; empty elsewhere)."""
    found: set[int] = set()
    try:
        entries = os.listdir("/proc")
    except OSError:
        return found
    for e in entries:
        if not e.isdigit():
            continue
        try:
            with open(f"/proc/{e}/stat", "rb") as f:   # bytes: a process name need not be valid UTF-8
                stat = f.read()
            fields = stat[stat.rfind(b")") + 2:].split()   # after "pid (comm)": state ppid pgrp ...
            if int(fields[1]) == pid and int(fields[2]) == int(e):
                found.add(int(e))
        except (OSError, ValueError, IndexError):
            continue
    return found


def _closed_error() -> RuntimeError:
    return RuntimeError("sandbox closed: this process is exiting (close_sandboxes)")


def _mkscratch(prefix: str) -> str:
    """A scratch directory (private, like ``tempfile.mkdtemp``), registered before it is created, so that
    close_sandboxes() can remove it even if a signal interrupts its creation or its user never gets to."""
    me = os.getpid()
    base = os.path.abspath(tempfile.gettempdir())   # absolute, as mkdtemp returns since Python 3.12
    while True:
        d = os.path.join(base, f"{prefix}{secrets.token_hex(5)}")
        state = ""   # "made": os.mkdir may have run; "taken": the name is someone else's, never remove it
        try:
            # under the lock: close_sandboxes() (from another thread, say) waits for a creation in progress
            with _LIVE_LOCK:
                if me in _CLOSED:
                    raise _closed_error()
                _LIVE_DIRS.add((me, d))
                state = "made"
                try:
                    os.mkdir(d, 0o700)
                except FileExistsError:   # draw another name
                    state = "taken"
                    _LIVE_DIRS.discard((me, d))
        except BaseException:
            # failed, or a signal surfacing at the lock's release: remove what may have been made first (it stays
            # registered until it is gone), then forget it. Not under the lock: under a tracer (pdb) a signal can
            # surface here while this thread still holds it, and set.discard needs no lock
            if state == "made":
                shutil.rmtree(d, ignore_errors=True)
            _LIVE_DIRS.discard((me, d))
            raise
        if state == "made":
            return d


def _rmscratch(d: str) -> None:
    shutil.rmtree(d, ignore_errors=True)
    with _LIVE_LOCK:
        _LIVE_DIRS.discard((os.getpid(), d))


def _kill_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, 9)
    except (ProcessLookupError, PermissionError):
        pass


def _cpu_backstop(timeout_s: float) -> Optional[int]:
    """RLIMIT_CPU for a child given only a wall timeout: a backstop, not the timeout. If this process dies without
    killing the child (SIGKILL), a CPU-bound child still stops; a blocked or sleeping one is not stopped by it.
    RLIMIT_CPU counts every thread, so it allows every core the child may use (at least 4) for the whole wall budget,
    plus 5 s: a child is never stopped by it before its wall timeout. It never exceeds the soft limit the child
    would otherwise inherit (a lower inherited limit applies as it would without the backstop)."""
    cores = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else (os.cpu_count() or 1)
    cpu = math.ceil(timeout_s) * max(4, cores) + 5
    soft = resource.getrlimit(resource.RLIMIT_CPU)[0]
    if soft != resource.RLIM_INFINITY:   # never more than the limit the child would otherwise inherit
        cpu = min(cpu, soft)
    return cpu if cpu > 0 else None


SAFE_ENV_KEYS = ("PATH", "HOME", "LANG", "LC_ALL", "PYTHONPATH", "TMPDIR", "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS",
                 "MKL_NUM_THREADS")


def run_cmd(
    cmd: Sequence[str],
    *,
    cwd: Optional[str | Path] = None,
    timeout_s: float = 60.0,
    cpu_s: Optional[int] = None,
    mem_mb: Optional[int] = 4096,
    env: Optional[Mapping[str, str]] = None,
    stdin: Optional[str] = None,
) -> RunResult:
    base_env = {k: os.environ[k] for k in SAFE_ENV_KEYS if k in os.environ}
    base_env.setdefault("OMP_NUM_THREADS", "1")
    base_env.setdefault("OPENBLAS_NUM_THREADS", "1")
    base_env.setdefault("MKL_NUM_THREADS", "1")
    # a fixed string-hash seed: printed sets and dicts keyed by strings come out in the same order on every run,
    # so tool output fed back to a model (and the model's cached reply to it) does not vary between runs
    base_env.setdefault("PYTHONHASHSEED", "0")
    if env:
        base_env.update(env)
    t0 = time.time()
    with _LIVE_LOCK:   # spawn and register in one step, so close_sandboxes() never misses a child
        if os.getpid() in _CLOSED:
            raise _closed_error()
        proc = subprocess.Popen(
            list(cmd), cwd=str(cwd) if cwd else None, env=base_env,
            stdin=subprocess.PIPE if stdin is not None else None,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True,  # own group: killpg
        )
        live = (os.getpid(), proc.pid)
        _LIVE_GROUPS.add(live)
    if cpu_s is None and timeout_s and math.isfinite(timeout_s):
        cpu_s = _cpu_backstop(timeout_s)
    _apply_limits(proc.pid, cpu_s, mem_mb)
    try:
        out, err = proc.communicate(input=stdin, timeout=timeout_s)
        return RunResult(proc.returncode, out, err, time.time() - t0, False)
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        out, err = proc.communicate()
        return RunResult(-9, out or "", (err or "") + "\n[killed: timeout]", time.time() - t0, True)
    except BaseException:
        # interrupted while waiting (KeyboardInterrupt, a harness watchdog...): never leave an orphan running
        _kill_group(proc)
        try:
            proc.communicate(timeout=5)
        except BaseException:  # noqa: BLE001 - best effort reaping
            pass
        raise
    finally:
        if proc.returncode is not None:   # reaped; one that is not stays registered for close_sandboxes()
            with _LIVE_LOCK:
                _LIVE_GROUPS.discard(live)


def run_python(
    code: Optional[str] = None,
    *,
    script: Optional[str | Path] = None,
    args: Sequence[str] = (),
    cwd: Optional[str | Path] = None,
    timeout_s: float = 60.0,
    mem_mb: Optional[int] = 4096,
    env: Optional[Mapping[str, str]] = None,
    stdin: Optional[str] = None,
) -> RunResult:
    """Run ``code`` (a string) or ``script`` (a path) with the current interpreter.

    With no ``cwd`` the code runs in a fresh scratch directory that is deleted
    afterwards (files it writes there do not survive the call)."""
    if code is None and script is None:
        raise ValueError("need code or script")
    tmpdir = None
    if cwd is None:
        if code is None:
            script = Path(script).resolve()  # a relative script path means relative to the caller, not the scratch dir
        tmpdir = _mkscratch("rsi_sbx_")
        cwd = tmpdir
    try:
        if code is not None:
            path = Path(cwd) / "_rsi_main.py"
            path.write_text(code)
            script = path
        rr = run_cmd([sys.executable, str(script), *args], cwd=cwd, timeout_s=timeout_s, mem_mb=mem_mb, env=env,
                     stdin=stdin)
        return _stable_paths(rr, tmpdir) if tmpdir is not None else rr
    finally:
        if tmpdir is not None:
            _rmscratch(tmpdir)


#: what a fresh scratch directory is called in captured output
SCRATCH_PLACEHOLDER = "/tmp/rsi_sandbox"


def _stable_paths(rr: RunResult, scratch: str) -> RunResult:
    """Replace the random scratch-directory path in ``rr``'s output with :data:`SCRATCH_PLACEHOLDER`.

    Tracebacks name the file they ran (``/tmp/rsi_sbx_k3j9x/_rsi_main.py``), and that text reaches model prompts
    (a harness feeding tool output back to its model, a proposer reading traces). A random path there makes
    identical runs send different prompts, which defeats exact replay from an LLM cache."""
    real = os.path.realpath(scratch)
    out, err = rr.stdout, rr.stderr
    for p in sorted({scratch, real}, key=len, reverse=True):  # longest first: one can contain the other
        out, err = out.replace(p, SCRATCH_PLACEHOLDER), err.replace(p, SCRATCH_PLACEHOLDER)
    return dataclasses.replace(rr, stdout=out, stderr=err)


def call_function(
    module_code: str,
    func: str,
    payload: dict,
    *,
    timeout_s: float = 30.0,
    mem_mb: Optional[int] = 2048,
    extra_files: Optional[Mapping[str, str]] = None,
) -> tuple[Optional[object], RunResult]:
    """Import ``module_code`` in a subprocess, call ``func(**payload)`` and return
    its JSON-serializable result. Used to run LLM-written functions (solvers,
    policies, harness entry points) without importing them into the loop.
    ``func`` may be a dotted attribute path (``"Solver.solve"``). Raises
    ValueError for an invalid ``func`` or an ``extra_files`` name that is
    absolute or escapes the scratch directory."""
    if not func or not all(part.isidentifier() for part in func.split(".")):
        raise ValueError(f"func must be a (dotted) Python identifier, got {func!r}")
    bad = [n for n in (extra_files or {}) if not is_safe_relpath(n)]
    if bad:  # extra files often come from an artifact: they must stay inside the scratch dir
        raise ValueError(f"unsafe extra_files names (absolute or escaping the directory): {bad[:5]}")
    d = _mkscratch("rsi_fn_")
    try:
        res, rr = _call_in(d, module_code, func, payload, timeout_s, mem_mb, extra_files)
        return res, _stable_paths(rr, d)
    finally:
        _rmscratch(d)


def _call_in(d, module_code, func, payload, timeout_s, mem_mb, extra_files):
    Path(d, "candidate.py").write_text(module_code)
    for name, text in (extra_files or {}).items():
        p = Path(d, name)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    # The result marker carries a per-call nonce the candidate never receives as data, and the
    # runner hard-exits right after writing it, so candidate code cannot forge or append a result
    # (e.g. from an atexit hook). Best effort, not a security boundary against frame inspection.
    runner = (
        "import json, os, sys\n"
        "sys.path.insert(0, '.')\n"
        "def _main():\n"
        "    msg = json.loads(sys.stdin.read())\n"
        "    tag = '__RSI_RESULT_' + msg.pop('nonce') + '__'\n"
        "    import candidate\n"
        f"    res = candidate.{func}(**msg['payload'])\n"
        "    out = json.dumps(res, default=float)\n"
        "    sys.stdout.flush()\n"
        "    os.write(1, ('\\n' + tag + out + '\\n').encode())\n"
        "    os._exit(0)\n"
        "_main()\n"
    )
    Path(d, "_runner.py").write_text(runner)
    nonce = secrets.token_hex(16)
    tag = f"__RSI_RESULT_{nonce}__"
    rr = run_cmd([sys.executable, "_runner.py"], cwd=d, timeout_s=timeout_s, mem_mb=mem_mb,
                 stdin=json.dumps({"nonce": nonce, "payload": payload}))
    if rr.ok and tag in rr.stdout:
        try:
            return json.loads(rr.stdout.rsplit(tag, 1)[1].strip().splitlines()[0]), rr
        except (json.JSONDecodeError, IndexError):
            pass
    return None, rr
