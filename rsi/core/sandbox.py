"""Run machine-written code in a subprocess with a timeout, rlimits, a scratch dir and a scrubbed env (no security)."""
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


def _apply_limits(pid: int, cpu_s: Optional[int], mem_mb: Optional[int]) -> None:
    """Set CPU / address-space rlimits on a just-started child (``preexec_fn`` can deadlock with threads)."""
    # each limit on its own: one that cannot be set never drops the other
    if cpu_s:
        hard = resource.getrlimit(resource.RLIMIT_CPU)[1]   # never above a hard limit this process may not raise
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
    # another thread may have held the parent's lock at the fork; nothing in the child would release it
    global _LIVE_LOCK
    _LIVE_LOCK = threading.Lock()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_fresh_lock_after_fork)


def close_sandboxes() -> int:
    """For a process about to exit: refuse new sandboxes, kill its live sandbox groups, remove its scratch dirs.

    Groups and directories a parent made before a fork are left alone. Returns how many groups it killed.
    """
    me = os.getpid()
    with _LIVE_LOCK:
        _CLOSED.add(me)
        # copies (one C call each): an unlocked discard (see _mkscratch) may change the sets meanwhile
        groups = {g for owner, g in _LIVE_GROUPS.copy() if owner == me}
        dirs = [d for owner, d in _LIVE_DIRS.copy() if owner == me]
    for g in groups:
        _killpg(g)
    for d in dirs:
        shutil.rmtree(d, ignore_errors=True)
    # also catch a session leader an interrupted run_cmd never registered; after the registered ones, so a failing
    # scan can never stop those
    extra = kill_child_sessions(me) - groups
    return len(groups) + len(extra)


def _killpg(g: int) -> None:
    try:
        os.killpg(g, 9)
    except (ProcessLookupError, PermissionError):
        pass


def kill_child_sessions(pid: int) -> set[int]:
    """Kill the group of every direct child of ``pid`` that leads its own (every sandbox does); return the groups."""
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
    """A private scratch dir, registered before it is created so close_sandboxes() can always remove it."""
    me = os.getpid()
    base = os.path.abspath(tempfile.gettempdir())
    while True:
        d = os.path.join(base, f"{prefix}{secrets.token_hex(5)}")
        state = ""   # "made": os.mkdir may have run; "taken": the name is someone else's, never remove it
        try:
            with _LIVE_LOCK:   # close_sandboxes() waits for a creation in progress
                if me in _CLOSED:
                    raise _closed_error()
                _LIVE_DIRS.add((me, d))
                state = "made"
                try:
                    os.mkdir(d, 0o700)
                except FileExistsError:
                    state = "taken"
                    _LIVE_DIRS.discard((me, d))
        except BaseException:
            # a failure or a signal at the lock's release: remove what may exist, then forget it. Unlocked, since a
            # signal under a tracer can surface while this thread holds the lock; one set.discard is atomic.
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


def _cpu_backstop(timeout_s: float) -> Optional[int]:
    """RLIMIT_CPU for a child given only a wall timeout, so a CPU-bound orphan still stops.

    It allows every core (at least 4) for the whole wall budget plus 5 s, and never exceeds the inherited soft limit.
    """
    cores = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else (os.cpu_count() or 1)
    cpu = math.ceil(timeout_s) * max(4, cores) + 5
    soft = resource.getrlimit(resource.RLIMIT_CPU)[0]
    if soft != resource.RLIM_INFINITY:
        cpu = min(cpu, soft)
    return cpu if cpu > 0 else None


SAFE_ENV_KEYS = ("PATH", "HOME", "LANG", "LC_ALL", "PYTHONPATH", "TMPDIR", "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS",
                 "MKL_NUM_THREADS")


def run_cmd(cmd: Sequence[str], *, cwd: Optional[str | Path] = None, timeout_s: float = 60.0,
            cpu_s: Optional[int] = None, mem_mb: Optional[int] = 4096, env: Optional[Mapping[str, str]] = None,
            stdin: Optional[str] = None) -> RunResult:
    base_env = {k: os.environ[k] for k in SAFE_ENV_KEYS if k in os.environ}
    for k in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
        base_env.setdefault(k, "1")
    # a fixed hash seed: printed sets/dicts come out in one order, so model-facing tool output is replayable
    base_env.setdefault("PYTHONHASHSEED", "0")
    base_env.update(env or {})
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
        _killpg(proc.pid)
        out, err = proc.communicate()
        return RunResult(-9, out or "", (err or "") + "\n[killed: timeout]", time.time() - t0, True)
    except BaseException:
        _killpg(proc.pid)   # interrupted while waiting: never leave an orphan running
        try:
            proc.communicate(timeout=5)
        except BaseException:  # noqa: BLE001 - best effort reaping
            pass
        raise
    finally:
        if proc.returncode is not None:   # reaped; one that is not stays registered for close_sandboxes()
            with _LIVE_LOCK:
                _LIVE_GROUPS.discard(live)


def run_python(code: Optional[str] = None, *, script: Optional[str | Path] = None, args: Sequence[str] = (),
               cwd: Optional[str | Path] = None, timeout_s: float = 60.0, mem_mb: Optional[int] = 4096,
               env: Optional[Mapping[str, str]] = None, stdin: Optional[str] = None) -> RunResult:
    """Run ``code`` or ``script`` with this interpreter; with no ``cwd``, in a scratch dir deleted afterwards."""
    if code is None and script is None:
        raise ValueError("need code or script")
    tmpdir = None
    if cwd is None:
        if code is None:
            script = Path(script).resolve()  # relative to the caller, not to the scratch dir
        tmpdir = cwd = _mkscratch("rsi_sbx_")
    try:
        if code is not None:
            script = Path(cwd) / "_rsi_main.py"
            script.write_text(code)
        rr = run_cmd([sys.executable, str(script), *args], cwd=cwd, timeout_s=timeout_s, mem_mb=mem_mb, env=env,
                     stdin=stdin)
        return _stable_paths(rr, tmpdir) if tmpdir is not None else rr
    finally:
        if tmpdir is not None:
            _rmscratch(tmpdir)


#: what a fresh scratch directory is called in captured output
SCRATCH_PLACEHOLDER = "/tmp/rsi_sandbox"


def _stable_paths(rr: RunResult, scratch: str) -> RunResult:
    """Replace the random scratch path in ``rr``'s output, so identical runs send identical prompts (cache replay)."""
    out, err = rr.stdout, rr.stderr
    for p in sorted({scratch, os.path.realpath(scratch)}, key=len, reverse=True):  # longest first: nested paths
        out, err = out.replace(p, SCRATCH_PLACEHOLDER), err.replace(p, SCRATCH_PLACEHOLDER)
    return dataclasses.replace(rr, stdout=out, stderr=err)


def call_function(module_code: str, func: str, payload: dict, *, timeout_s: float = 30.0,
                  mem_mb: Optional[int] = 2048, extra_files: Optional[Mapping[str, str]] = None,
                  ) -> tuple[Optional[object], RunResult]:
    """Import ``module_code`` in a subprocess and return ``(func(**payload), run_result)``; JSON results only.

    ``func`` may be dotted (``"Solver.solve"``); an invalid ``func`` or unsafe ``extra_files`` name raises ValueError.
    """
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
    # The result marker carries a per-call nonce the candidate never sees, and the runner hard-exits right after
    # writing it, so candidate code cannot forge or append a result (best effort, not against frame inspection).
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
