"""Run untrusted, machine-written code safely-ish.

Self-improving loops execute code an LLM just wrote (training scripts, solvers,
harnesses, search policies, validation commands). :func:`run_python` runs it in a
separate process with a wall-clock timeout, CPU/memory rlimits, a scratch working
directory and a scrubbed environment. This is isolation against accidents
(infinite loops, OOM, stray writes), not a security boundary against a
determined adversary - use containers for that.
"""
from __future__ import annotations

import json
import os
import resource
import secrets
import shutil
import subprocess
import sys
import tempfile
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
    try:
        if cpu_s:
            resource.prlimit(pid, resource.RLIMIT_CPU, (cpu_s, cpu_s + 5))
        if mem_mb:
            b = mem_mb * 1024 * 1024
            resource.prlimit(pid, resource.RLIMIT_AS, (b, b))
    except (ProcessLookupError, PermissionError):
        pass   # the child already exited (a zombie cannot be limited, and needs no limits)


def _kill_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, 9)
    except (ProcessLookupError, PermissionError):
        pass


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
    if env:
        base_env.update(env)
    t0 = time.time()
    proc = subprocess.Popen(
        list(cmd), cwd=str(cwd) if cwd else None, env=base_env, stdin=subprocess.PIPE if stdin is not None else None,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True,  # own group: killpg
    )
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
        tmpdir = tempfile.mkdtemp(prefix="rsi_sbx_")
        cwd = tmpdir
    try:
        if code is not None:
            path = Path(cwd) / "_rsi_main.py"
            path.write_text(code)
            script = path
        return run_cmd([sys.executable, str(script), *args], cwd=cwd, timeout_s=timeout_s, mem_mb=mem_mb, env=env,
                       stdin=stdin)
    finally:
        if tmpdir is not None:
            shutil.rmtree(tmpdir, ignore_errors=True)


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
    d = tempfile.mkdtemp(prefix="rsi_fn_")
    try:
        return _call_in(d, module_code, func, payload, timeout_s, mem_mb, extra_files)
    finally:
        shutil.rmtree(d, ignore_errors=True)


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
