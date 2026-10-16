"""rsi.core.sandbox (timeouts, process-group kill, rlimits, env scrub,
call_function, scratch-dir cleanup) and rsi.core.run (Budget, ImprovementResult,
usd_of, transfer_report)."""
from __future__ import annotations

import json
import signal
import sys
import tempfile
import time
from pathlib import Path

import pytest

from rsi.core import (Artifact, Budget, FunctionDomain, ImprovementResult, Ledger, MockLLM, Node, RunResult,
                      SealedSplitError, Task, TaskSuite, Usage, call_function, run_cmd, run_python, transfer_report,
                      usd_of)


@pytest.fixture
def scratch_tmp(tmp_path, monkeypatch):
    """Point tempfile at a private dir so leaked scratch dirs are observable."""
    d = tmp_path / "tmp"
    d.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(d))
    return d


# ---------------------------------------------------------------------- run_cmd
def test_run_cmd_basic_stdin_cwd_and_exit_codes(tmp_path):
    rr = run_cmd([sys.executable, "-c", "import sys, os; print(sys.stdin.read().upper()); print(os.getcwd())"],
                 stdin="hello", cwd=tmp_path)
    assert rr.ok and rr.returncode == 0 and not rr.timed_out
    assert rr.stdout.splitlines() == ["HELLO", str(tmp_path)] and rr.wall_s >= 0
    bad = run_cmd([sys.executable, "-c", "import sys; print('out'); print('err', file=sys.stderr); sys.exit(3)"])
    assert not bad.ok and bad.returncode == 3 and bad.tail().split() == ["out", "err"]
    assert RunResult(0, "a\nb\nc", "", 0.0, False).tail(2) == "b\nc"


def test_run_cmd_scrubs_environment(monkeypatch):
    monkeypatch.setenv("RSI_TEST_SECRET", "s3cret")
    code = "import os; print(os.environ.get('RSI_TEST_SECRET'), os.environ.get('FOO'), os.environ.get('OMP_NUM_THREADS'))"
    assert run_cmd([sys.executable, "-c", code]).stdout.split() == ["None", "None", "1"]
    assert run_cmd([sys.executable, "-c", code], env={"FOO": "bar"}).stdout.split() == ["None", "bar", "1"]


def test_run_cmd_timeout_kills_whole_process_group():
    # the grandchild `sleep` inherits stdout; without a group kill communicate() would wait 30 s
    code = "import subprocess, time; subprocess.Popen(['sleep', '30']); print('started', flush=True); time.sleep(30)"
    t0 = time.time()
    rr = run_cmd([sys.executable, "-c", code], timeout_s=1.0)
    assert time.time() - t0 < 10
    assert rr.timed_out and not rr.ok and rr.returncode == -9
    assert "[killed: timeout]" in rr.stderr and "started" in rr.stdout


def test_run_cmd_memory_rlimit():
    rr = run_cmd([sys.executable, "-c", "x = bytearray(600 * 1024 * 1024); print('allocated')"], mem_mb=300)
    assert not rr.ok and "allocated" not in rr.stdout and "MemoryError" in rr.stderr
    assert run_cmd([sys.executable, "-c", "x = bytearray(50 * 1024 * 1024); print('ok')"], mem_mb=300).ok


def test_run_cmd_cpu_rlimit():
    t0 = time.time()
    rr = run_cmd([sys.executable, "-c", "while True: pass"], cpu_s=1, timeout_s=30)
    assert time.time() - t0 < 15
    assert not rr.ok and not rr.timed_out
    assert rr.returncode in (-signal.SIGXCPU, -signal.SIGKILL)


# ------------------------------------------------------------------- run_python
def test_run_python_code_and_script(tmp_path, scratch_tmp, monkeypatch):
    rr = run_python("import sys; print(sum(map(int, sys.argv[1:])))", args=["2", "3"])
    assert rr.ok and rr.stdout.strip() == "5"
    script = tmp_path / "s.py"
    script.write_text("print('from script')")
    assert run_python(script=script).stdout.strip() == "from script"
    monkeypatch.chdir(tmp_path)                        # a relative script is relative to the caller
    assert run_python(script="s.py").stdout.strip() == "from script"
    rr = run_python("open('out.txt', 'w').write('x'); print('wrote')", cwd=tmp_path)
    assert rr.ok and (tmp_path / "out.txt").exists() and (tmp_path / "_rsi_main.py").exists()
    assert run_python("import time; time.sleep(5)", timeout_s=0.5).timed_out
    with pytest.raises(ValueError):
        run_python()


def test_run_python_cleans_its_scratch_dir(scratch_tmp):
    """Bug fix: every call leaked a /tmp/rsi_sbx_* dir (48k were found on the dev box)."""
    for _ in range(3):
        run_python("open('junk.txt', 'w').write('x' * 1000)")
    assert list(scratch_tmp.iterdir()) == []


# ---------------------------------------------------------------- call_function
MODULE = '''
import json
def add(a, b):
    return {"sum": a + b, "floaty": 0.5}
def boom():
    raise ValueError("bad input")
def slow():
    import time; time.sleep(30)
def noisy():
    print("__RSI_RESULT__ fake marker")
    return [1, 2]
class Solver:
    @staticmethod
    def solve(x):
        return x * 2
def uses_helper():
    import helper
    return helper.VALUE
'''


def test_call_function_results_and_failures(scratch_tmp):
    res, rr = call_function(MODULE, "add", {"a": 2, "b": 3})
    assert res == {"sum": 5, "floaty": 0.5} and rr.ok
    res, rr = call_function(MODULE, "boom", {})
    assert res is None and not rr.ok and "ValueError: bad input" in rr.stderr
    res, rr = call_function(MODULE, "slow", {}, timeout_s=0.5)
    assert res is None and rr.timed_out
    assert call_function(MODULE, "noisy", {})[0] == [1, 2]          # the last marker wins
    assert call_function(MODULE, "Solver.solve", {"x": 21})[0] == 42
    assert call_function(MODULE, "uses_helper", {}, extra_files={"helper.py": "VALUE = 'h'"})[0] == "h"
    res, rr = call_function("syntax error here", "f", {})
    assert res is None and "SyntaxError" in rr.stderr
    assert list(scratch_tmp.iterdir()) == []                        # bug fix: no leaked rsi_fn_* dirs


@pytest.mark.parametrize("func", ["", "f; import os", "a..b", "1abc", "f()"])
def test_call_function_rejects_non_identifiers(func):
    with pytest.raises(ValueError):
        call_function(MODULE, func, {})


# ---------------------------------------------------------------------- Budget
def test_budget_limits(tmp_path):
    assert Budget().exhausted(rounds=10 ** 6, rollouts=10 ** 6, usd=1e6) is None
    b = Budget(max_rounds=3, max_rollouts=100, max_usd=1.0, stop_dir=str(tmp_path))
    assert b.exhausted(rounds=2, rollouts=99, usd=0.99) is None
    assert b.exhausted(rounds=3) == "max_rounds"
    assert b.exhausted(rollouts=100) == "max_rollouts"
    assert b.exhausted(usd=1.0) == "max_usd"
    (tmp_path / "STOP").write_text("")
    assert b.exhausted() == "stop_file"
    w = Budget(max_wall_s=0.05)
    assert w.exhausted() is None
    time.sleep(0.08)
    assert w.exhausted() == "max_wall_s"


def test_usd_of_and_improvement_result(tmp_path):
    a, b = MockLLM(), MockLLM()
    a.meter.add("task", Usage(1, 1, 1, 0.25))
    b.meter.add("proposer", Usage(1, 1, 1, 0.5))
    assert usd_of(a, None, b) == pytest.approx(0.75) and usd_of() == 0.0
    led = Ledger()
    led.add(Node("root", None, score=0.5))
    base, best = Artifact({"h.py": "1\n"}), Artifact({"h.py": "2\n", "sub/p.md": "p\n"})
    res = ImprovementResult("demo", base, best, led, trajectory=[{"round": 0, "S": 0.5}, {"round": 1, "S": 0.7}],
                            usage={"_total": {"calls": 2}}, stop_reason="max_rounds", out_dir=str(tmp_path / "o"),
                            meta={"obj": object()})
    s = res.summary()
    assert s["method"] == "demo" and s["rounds"] == 2 and s["best"] == best.short_id
    assert s["first"]["S"] == 0.5 and s["last"]["S"] == 0.7 and s["usage"] == {"calls": 2}
    d = res.save()
    assert Artifact.from_dir(d / "best_artifact") == best
    assert json.loads((d / "trajectory.json").read_text())[1]["S"] == 0.7
    assert json.loads((d / "summary.json").read_text())["stop_reason"] == "max_rounds"
    res.best = Artifact({"h.py": "3\n"})
    res.save()                                                     # best_artifact is replaced, not merged
    assert not (d / "best_artifact" / "sub").exists()


# -------------------------------------------------------------- transfer_report
def _transfer_domain():
    ts = [Task(f"e{i}", i, i, "num") for i in range(4)] + [Task(f"h{i}", i, i, "num") for i in range(4)] + \
         [Task(f"o{i}", i, i, "dates" if i % 2 else "strings") for i in range(4)]
    suite = TaskSuite(ts, {"evolve": [f"e{i}" for i in range(4)], "holdout": [f"h{i}" for i in range(4)],
                           "ood": [f"o{i}" for i in range(4)]})

    def execute(art, task, seed, llm):
        skill = int(art["skill"])
        return int(task.input < skill)

    return FunctionDomain(suite, execute, lambda t, out: float(out))


def test_transfer_report_unseals_for_reporting_only():
    dom = _transfer_domain()
    arms = {"seed": Artifact({"skill": "1"}), "evolved": Artifact({"skill": "3"})}
    rep = transfer_report(dom, None, arms, splits=("evolve", "holdout", "ood", "missing"), workers=1)
    assert rep["reference"] == "seed" and set(rep["splits"]) == {"evolve", "holdout", "ood"}
    assert rep["splits"]["holdout"]["seed"]["S"] == 0.25 and rep["splits"]["holdout"]["evolved"]["S"] == 0.75
    assert rep["splits"]["ood"]["seed"]["vs_reference"] is None
    cmp = rep["splits"]["ood"]["evolved"]["vs_reference"]
    assert cmp["mean_diff"] == pytest.approx(0.5) and cmp["n"] == 4
    assert rep["splits"]["ood"]["evolved"]["families"] == {"strings": 1.0, "dates": 0.5}
    assert rep["unseen_mean"] == {"seed": 0.25, "evolved": 0.75}
    with pytest.raises(SealedSplitError):                          # the suite itself stays sealed
        dom.tasks.split("holdout")
    rep2 = transfer_report(dom, None, arms, splits=("holdout",), reference="evolved", workers=1)
    assert rep2["splits"]["holdout"]["seed"]["vs_reference"]["mean_diff"] == pytest.approx(-0.5)
    assert "unseen_mean" not in transfer_report(dom, None, arms, splits=("evolve",), workers=1)


def test_child_gets_a_cpu_backstop_from_its_wall_timeout():
    """A CPU-bound child that outlives a SIGKILLed parent (no one left to enforce the wall timeout) still stops:
    without an explicit cpu_s it gets RLIMIT_CPU of the wall budget times the usable cores (at least 4), plus 5 s;
    an explicit cpu_s wins, and the memory limit is applied either way."""
    import os as _os
    import sys as _sys
    from rsi.core.sandbox import run_cmd
    cores = len(_os.sched_getaffinity(0))
    probe = [_sys.executable, "-c", "import resource as r; print(r.getrlimit(r.RLIMIT_CPU)[0], r.getrlimit(r.RLIMIT_AS)[0])"]
    assert run_cmd(probe, timeout_s=10, mem_mb=512).stdout.split() == [str(10 * max(4, cores) + 5), str(512 << 20)]
    assert run_cmd(probe, timeout_s=10, cpu_s=3, mem_mb=512).stdout.split() == ["3", str(512 << 20)]


def test_a_refused_cpu_limit_keeps_the_memory_limit(monkeypatch):
    """A CPU prlimit the kernel refuses (a hard limit this process may not raise) no longer skips the memory cap."""
    import resource as _resource
    import sys as _sys
    from rsi.core import sandbox
    real = _resource.prlimit

    def refuse_cpu(pid, res, limits=None):
        if res == _resource.RLIMIT_CPU and limits is not None:
            raise PermissionError("not permitted")
        return real(pid, res, limits) if limits is not None else real(pid, res)

    monkeypatch.setattr(sandbox.resource, "prlimit", refuse_cpu)
    probe = [_sys.executable, "-c", "import resource as r; print(r.getrlimit(r.RLIMIT_AS)[0])"]
    assert sandbox.run_cmd(probe, timeout_s=10, cpu_s=100, mem_mb=512).stdout.strip() == str(512 << 20)


def _checkout_python(code: str, **kw):
    """Run ``code`` in a fresh interpreter that imports this checkout's ``rsi``, from any working directory: the
    checkout is on PYTHONPATH and the interpreter starts there (so no other ``rsi`` in the caller's cwd shadows
    it)."""
    import os as _os
    import subprocess as _sp
    import sys as _sys
    root = str(Path(__file__).resolve().parents[2])
    env = {**_os.environ, "PYTHONPATH": _os.pathsep.join(p for p in (root, _os.environ.get("PYTHONPATH")) if p),
           **kw.pop("env", {})}
    return _sp.run([_sys.executable, "-c", code], capture_output=True, text=True, timeout=60, env=env,
                   cwd=kw.pop("cwd", root), **kw)


def test_backstop_stays_within_inherited_soft_and_hard_cpu_limits():
    """Under an inherited RLIMIT_CPU the backstop never raises the soft limit nor lowers it below what the child would
    inherit, fits under the hard one, and the memory limit is set as well."""
    import os as _os
    b = min(10 * max(4, len(_os.sched_getaffinity(0))) + 5, 1000)   # the backstop for a 10 s wall timeout here
    for limits, want in (((30, 30), "(30, 30)"), ((20, -1), "(20, 25)"), ((6, 6), "(6, 6)"),
                         ((1000, 1002), f"({b}, {min(b + 5, 1002)})")):
        code = (f"import resource as r, sys; r.setrlimit(r.RLIMIT_CPU, {limits}); from rsi.core.sandbox import run_cmd; "
                "p = [sys.executable, '-c', 'import resource as r; print(r.getrlimit(r.RLIMIT_CPU), r.getrlimit(r.RLIMIT_AS)[0])']; "
                "print(run_cmd(p, timeout_s=10, mem_mb=512).stdout.strip())")
        out = _checkout_python(code).stdout.strip()
        assert out == f"{want} {512 << 20}", (limits, out)


def test_a_closed_process_starts_no_sandbox():
    """close_sandboxes() (a process about to exit) refuses new sandboxes and scratch directories."""
    code = ("from rsi.core.sandbox import close_sandboxes, run_cmd, run_python; import sys; close_sandboxes()\n"
            "for f in (lambda: run_cmd([sys.executable, '-c', 'pass']), lambda: run_python('pass')):\n"
            "    try:\n        f(); print('started')\n    except RuntimeError as e:\n        print('refused')")
    out = _checkout_python(code).stdout.split()
    assert out == ["refused", "refused"], out


def test_close_sandboxes_catches_a_sandbox_run_cmd_never_registered():
    """A signal between spawn and registration (or between a failed kill and deregistration) leaves a sandbox
    the registry does not hold; close_sandboxes() still kills it: every sandbox leads its own session as a direct
    child."""
    code = ("import subprocess, sys, time, os\n"
            "from rsi.core.sandbox import close_sandboxes\n"
            "spin = 'import resource; resource.setrlimit(resource.RLIMIT_CPU, (20, 20))\\nwhile True: pass'\n"
            "p = subprocess.Popen([sys.executable, '-c', spin], start_new_session=True,\n"
            "                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
            "try:\n"
            "    time.sleep(0.2); close_sandboxes(); p.wait(timeout=5); print(p.returncode)\n"
            "finally:\n"
            "    if p.poll() is None:\n"
            "        os.killpg(p.pid, 9)")
    out = _checkout_python(code).stdout.strip()
    assert out == "-9", out


def _inner(code: str, tmp_path, **env) -> str:
    """Run ``code`` in a fresh interpreter (close_sandboxes marks a process closed for good) with its temp files
    under ``tmp_path``; return its stdout, or fail with its stderr."""
    tmp = tmp_path / "tmp"
    tmp.mkdir(exist_ok=True)
    out = _checkout_python(code, env={"TMPDIR": str(tmp), **env}, cwd=tmp_path)
    leftovers = [p.name for p in tmp.iterdir() if p.name.startswith(("rsi_sbx_", "rsi_fn_"))]
    return out.stdout.strip() + (f" leftovers={leftovers}" if leftovers else "") + (
        f" stderr={out.stderr[-1500:]}" if out.returncode else "")


_SPIN = r"""
import ctypes, os, subprocess, sys, threading, time
die_with_parent = 'import ctypes; ctypes.CDLL(None).prctl(1, 9)\n'   # PR_SET_PDEATHSIG: never outlive this script
spin = die_with_parent + 'import resource; resource.setrlimit(resource.RLIMIT_CPU, (20, 20))\nwhile True: pass'

def stat(pid):
    try:
        with open(f'/proc/{pid}/stat', 'rb') as f:
            return f.read()
    except OSError:
        return None

def live_session_children():
    out = []
    for d in os.listdir('/proc'):
        s = stat(d) if d.isdigit() else None
        if s is None:
            continue
        f = s[s.rfind(b')') + 2:].split()
        if int(f[1]) == os.getpid() and int(f[2]) == int(d) and f[0] != b'Z':
            out.append(int(d))
    return out

def settle(want_at_least, deadline=5):
    end = time.monotonic() + deadline
    while len(live_session_children()) < want_at_least and time.monotonic() < end:
        time.sleep(0.02)

def gone(deadline=5):
    end = time.monotonic() + deadline
    while (left := live_session_children()) and time.monotonic() < end:
        time.sleep(0.05)
    return left
"""


def test_close_sandboxes_is_not_stopped_by_a_process_name_that_is_not_utf8(tmp_path):
    """The kernel cuts a process name to 15 bytes, possibly inside a character: scanning /proc must still kill
    a sandbox with such a name, an unregistered one and the registered one."""
    code = _SPIN + r"""
from rsi.core.sandbox import close_sandboxes, run_cmd
name = bytes.fromhex('74656d70c3a9726174757265735fc3')          # 'temp' + e-acute + 'ratures_' + half a character
odd = subprocess.Popen([sys.executable, '-c', die_with_parent + 'import ctypes, sys, time; '
                        'ctypes.CDLL(None).prctl(15, ctypes.c_char_p(bytes.fromhex(sys.argv[1])), 0, 0, 0); '
                        'time.sleep(30)', name.hex()], start_new_session=True)
loose = subprocess.Popen([sys.executable, '-c', spin], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
threading.Thread(target=run_cmd, args=([sys.executable, '-c', spin],), kwargs={'timeout_s': 30}, daemon=True).start()
try:
    end = time.monotonic() + 5
    while b'\xc3)' not in (stat(odd.pid) or b''):
        if time.monotonic() > end:
            raise SystemExit('the odd process name never appeared')
        time.sleep(0.02)
    settle(3)
    close_sandboxes()
    odd.wait(timeout=5); loose.wait(timeout=5)
    print(odd.returncode, loose.returncode, gone())
finally:
    for p in (odd, loose):
        if p.poll() is None:
            os.killpg(p.pid, 9)
"""
    assert _inner(code, tmp_path) == "-9 -9 []"


def test_a_failing_proc_scan_does_not_spare_the_registered_sandboxes(tmp_path):
    """close_sandboxes() kills the registered groups and removes their directories before it scans /proc."""
    code = _SPIN + r"""
from rsi.core import sandbox
sandbox._rmscratch = lambda d: None      # only close_sandboxes() may remove the directory
threading.Thread(target=sandbox.run_python, args=(spin,), kwargs={'timeout_s': 30}, daemon=True).start()
settle(1)
def broken(pid):
    raise RuntimeError('scan failed')
sandbox._child_session_leaders = broken
try:
    sandbox.close_sandboxes()
except RuntimeError as e:
    print(e, end=' ')
print(gone())
"""
    assert _inner(code, tmp_path) == "scan failed []"


def test_a_signal_while_a_scratch_directory_is_made_cannot_hide_it(tmp_path):
    """The directory is registered before it exists, and a failed creation stays registered until it is gone:
    a stop signal as it appears, then another one during the cleanup, still leave it to close_sandboxes()."""
    code = r"""
import os
from rsi.core import sandbox
seen = []
real_mkdir, real_rmtree = os.mkdir, sandbox.shutil.rmtree
def mkdir(path, *a, **k):
    seen.append(any(p == path for _, p in sandbox._LIVE_DIRS))   # registered before it exists
    real_mkdir(path, *a, **k)
    raise KeyboardInterrupt                                        # a stop signal the moment it appears
calls = []
def rmtree(path, *a, **k):
    calls.append(path)
    if len(calls) == 1:
        raise SystemExit(143)                                      # a second one during the cleanup
    return real_rmtree(path, *a, **k)
os.mkdir, sandbox.shutil.rmtree = mkdir, rmtree
try:
    sandbox.run_python('pass')
except SystemExit:
    pass
os.mkdir = real_mkdir
sandbox.close_sandboxes()
print(seen)
"""
    assert _inner(code, tmp_path) == "[True]"


def test_a_scratch_directory_made_by_another_thread_while_closing_is_removed(tmp_path):
    """close_sandboxes() waits for a creation in progress in another thread (as AgentQA's harness thread)."""
    code = r"""
import os, threading, time
from rsi.core import sandbox
real_mkdir = os.mkdir
started = threading.Event()
def slow_mkdir(path, *a, **k):
    started.set()
    time.sleep(0.5)
    real_mkdir(path, *a, **k)
os.mkdir = slow_mkdir
sandbox.run_cmd = lambda *a, **k: time.sleep(30)     # the thread never gets back to its own cleanup
threading.Thread(target=sandbox.run_python, args=('pass',), daemon=True).start()
started.wait(5)
sandbox.close_sandboxes()
time.sleep(1.0)
print('done', flush=True)
os._exit(0)                                          # as the forked smoke child leaves: no thread cleans up
"""
    assert _inner(code, tmp_path) == "done"


def test_run_python_works_with_a_relative_temp_dir(tmp_path):
    """Scratch paths are absolute (as mkdtemp's since Python 3.12), so a relative TMPDIR still works."""
    code = "from rsi.core.sandbox import run_python; print(run_python('print(42)').stdout.strip())"
    assert _inner(code, tmp_path, TMPDIR=".") == "42"             # gettempdir() keeps "." relative


def test_a_signal_as_the_scratch_lock_is_released_leaves_no_directory(tmp_path):
    """A stop signal can surface when the lock is released after the directory was made: the directory is
    removed and forgotten, not left for a close_sandboxes() that a long-lived process never calls."""
    code = r"""
import os
from rsi.core import sandbox
real = sandbox._LIVE_LOCK
class Lock:
    exits = 0
    def __enter__(self):
        return real.__enter__()
    def __exit__(self, *exc):
        real.__exit__(*exc)
        Lock.exits += 1
        if Lock.exits == 1 and exc[0] is None:
            raise KeyboardInterrupt   # as a handler would at the release
sandbox._LIVE_LOCK = Lock()
try:
    sandbox.run_python('pass')
    print('returned', end=' ')
except KeyboardInterrupt:
    print('interrupted', end=' ')
print(len(sandbox._LIVE_DIRS))
"""
    assert _inner(code, tmp_path) == "interrupted 0"


def test_a_signal_while_the_scratch_lock_is_still_held_does_not_hang(tmp_path):
    """Under a tracer (pdb) a stop signal can surface while this thread still holds the registry lock: the
    cleanup must not wait for that lock (it used to deadlock on the first Ctrl-C)."""
    code = r"""
import threading
from rsi.core import sandbox
real = sandbox._LIVE_LOCK
class Lock:
    exits = 0
    def __enter__(self):
        return real.__enter__()
    def __exit__(self, *exc):
        Lock.exits += 1
        if Lock.exits == 1 and exc[0] is None:
            raise KeyboardInterrupt   # before the release, the lock still held by this thread
        return real.__exit__(*exc)
    def acquire(self, *a, **k):
        return real.acquire(*a, **k)
    def release(self):
        return real.release()
sandbox._LIVE_LOCK = Lock()
out = []
def go():
    try:
        sandbox.run_python('pass')
        out.append('returned')
    except KeyboardInterrupt:
        out.append('interrupted')
t = threading.Thread(target=go, daemon=True)
t.start()
t.join(10)
print(out[0] if out else 'hung', len(sandbox._LIVE_DIRS))
"""
    assert _inner(code, tmp_path) == "interrupted 0"


def test_close_sandboxes_iterates_a_copy_of_the_registry(tmp_path):
    """_mkscratch's cleanup discards from the registry without the lock: close_sandboxes must not iterate the
    live set while that can happen (it raised 'Set changed size during iteration')."""
    code = r"""
from rsi.core import sandbox
class Live(set):
    def __iter__(self):
        it = super().__iter__()
        first = next(it)
        self.discard(first)            # as another thread's unlocked discard would, mid-iteration
        yield first
        yield from it
sandbox._LIVE_DIRS = Live({(1, '/nonexistent/a'), (1, '/nonexistent/b')})
try:
    sandbox.close_sandboxes()
    print('ok')
except RuntimeError as e:
    print('RuntimeError', e)
"""
    assert _inner(code, tmp_path) == "ok"
