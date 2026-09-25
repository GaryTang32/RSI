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
