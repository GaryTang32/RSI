"""Sandbox output must not carry the random scratch-directory path (it reaches model prompts via tracebacks)."""
from rsi.core.sandbox import SCRATCH_PLACEHOLDER, call_function, run_python


def test_traceback_names_a_stable_path():
    a, b = (run_python("raise ValueError('boom')", timeout_s=20) for _ in range(2))
    assert not a.ok and a.stderr == b.stderr
    assert f"{SCRATCH_PLACEHOLDER}/_rsi_main.py" in a.stderr and "rsi_sbx_" not in a.stderr


def test_printed_cwd_is_stable_too():
    a, b = (run_python("import os; print(os.getcwd())", timeout_s=20) for _ in range(2))
    assert a.stdout.strip() == b.stdout.strip() == SCRATCH_PLACEHOLDER


def test_call_function_errors_are_stable():
    code = "def f(x):\n    raise RuntimeError('no')\n"
    (r1, rr1), (r2, rr2) = (call_function(code, "f", {"x": 1}, timeout_s=20) for _ in range(2))
    assert r1 is None and r2 is None and rr1.stderr == rr2.stderr and "rsi_fn_" not in rr1.stderr


def test_symlinked_scratch_is_replaced_whatever_the_hash_seed(checkout_python):
    # macOS /tmp -> /private/tmp: the longer real path must be replaced first, in an order independent of set iteration
    code = ("from rsi.core import sandbox\n"
            "from rsi.core.sandbox import RunResult, _stable_paths\n"
            "sandbox.os.path.realpath = lambda p: '/private' + p\n"
            "rr = _stable_paths(RunResult(1, '', 'File \"/private/tmp/rsi_sbx_k3/_rsi_main.py\"', 0.0, False), "
            "'/tmp/rsi_sbx_k3')\n"
            "print(rr.stderr)\n")
    outs = {checkout_python(code, env={"PYTHONHASHSEED": str(seed)}).stdout.strip() for seed in range(8)}
    assert outs == {f'File "{SCRATCH_PLACEHOLDER}/_rsi_main.py"'}


def test_set_order_in_sandbox_output_is_stable():
    code = "print({'red', 'green', 'blue', 'white', 'yellow', 'cyan', 'magenta'})"
    assert len({run_python(code, timeout_s=20).stdout for _ in range(4)}) == 1
    hash_seed = "import os; print(os.environ['PYTHONHASHSEED'])"
    assert run_python(hash_seed, timeout_s=20).stdout.strip() == "0"      # the default unless the caller sets it
    assert run_python(hash_seed, timeout_s=20, env={"PYTHONHASHSEED": "7"}).stdout.strip() == "7"
