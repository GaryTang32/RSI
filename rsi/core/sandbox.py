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
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, Sequence


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
    def apply():
        os.setsid()
        if cpu_s:
            resource.setrlimit(resource.RLIMIT_CPU, (cpu_s, cpu_s + 5))
        if mem_mb:
            b = mem_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (b, b))
    return apply


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
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, preexec_fn=_limits(cpu_s, mem_mb),
    )
    try:
        out, err = proc.communicate(input=stdin, timeout=timeout_s)
        return RunResult(proc.returncode, out, err, time.time() - t0, False)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, 9)
        except ProcessLookupError:
            pass
        out, err = proc.communicate()
        return RunResult(-9, out or "", (err or "") + "\n[killed: timeout]", time.time() - t0, True)


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
    ``func`` may be a dotted attribute path (``"Solver.solve"``)."""
    if not func or not all(part.isidentifier() for part in func.split(".")):
        raise ValueError(f"func must be a (dotted) Python identifier, got {func!r}")
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
    runner = (
        "import json, sys\n"
        "sys.path.insert(0, '.')\n"
        "payload = json.loads(sys.stdin.read())\n"
        "import candidate\n"
        f"res = candidate.{func}(**payload)\n"
        "sys.stdout.write('\\n__RSI_RESULT__' + json.dumps(res, default=float))\n"
    )
    Path(d, "_runner.py").write_text(runner)
    rr = run_cmd([sys.executable, "_runner.py"], cwd=d, timeout_s=timeout_s, mem_mb=mem_mb, stdin=json.dumps(payload))
    if rr.ok and "__RSI_RESULT__" in rr.stdout:
        try:
            return json.loads(rr.stdout.rsplit("__RSI_RESULT__", 1)[1]), rr
        except json.JSONDecodeError:
            pass
    return None, rr
