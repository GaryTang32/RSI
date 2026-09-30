"""Sandbox output must not carry the random scratch-directory path (it reaches model prompts via tracebacks)."""
from rsi.core.sandbox import SCRATCH_PLACEHOLDER, call_function, run_python


def test_traceback_names_a_stable_path():
    a = run_python("raise ValueError('boom')", timeout_s=20)
    b = run_python("raise ValueError('boom')", timeout_s=20)
    assert not a.ok and a.stderr == b.stderr
    assert f"{SCRATCH_PLACEHOLDER}/_rsi_main.py" in a.stderr and "rsi_sbx_" not in a.stderr


def test_printed_cwd_is_stable_too():
    a, b = (run_python("import os; print(os.getcwd())", timeout_s=20) for _ in range(2))
    assert a.stdout.strip() == b.stdout.strip() == SCRATCH_PLACEHOLDER


def test_call_function_errors_are_stable():
    code = "def f(x):\n    raise RuntimeError('no')\n"
    (r1, rr1), (r2, rr2) = (call_function(code, "f", {"x": 1}, timeout_s=20) for _ in range(2))
    assert r1 is None and r2 is None and rr1.stderr == rr2.stderr and "rsi_fn_" not in rr1.stderr
