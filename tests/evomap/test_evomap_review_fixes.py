"""Regression tests for the EvoMap findings of the November 2026 code review (command policy, SafeHub paths)."""

from __future__ import annotations

import pytest
from evomap_helpers import honest_bundle, safe_hub

from rsi.core import MockLLM
from rsi.core.llm import CachedLLM, ClaudeCLI
from rsi.evomap.safehub import _is_real_model
from rsi.evomap.validation import CommandPolicy, InProcessExecutor, SubprocessExecutor


@pytest.mark.parametrize("policy", [CommandPolicy.faithful(), CommandPolicy.safe()])
@pytest.mark.parametrize(
    "cmd",
    [
        "python -Ic \"open('/tmp/x','w').write('pwned')#.py\"",  # -c combined with -I
        'python3 -Sc "print(1)#.py"',
        "python -Im json.tool x.py",  # -m combined
        'python -c "print(1)"',
        "python -m pytest",
    ],
)
def test_combined_short_flags_cannot_smuggle_a_blocked_option(policy, cmd):
    assert not policy.check(cmd).ok


def test_ordinary_commands_still_pass():
    p = CommandPolicy.safe()
    assert p.check("python check.py").ok
    assert p.check("python -u check.py").ok  # an allowed single flag
    assert p.check("python --version").info_only
    assert not p.check("pytest -qp no:cacheprovider test_x.py").ok  # -p hidden in a cluster


def test_subprocess_executor_refuses_workspace_files_outside_its_scratch_dir(tmp_path):
    marker = tmp_path / "escaped.txt"
    out = SubprocessExecutor(cache=False)(["python", "check.py"], {"check.py": "print(1)", str(marker): "x"})
    assert out.exit != 0 and "outside the workspace" in out.stderr and not marker.exists()
    out = SubprocessExecutor(cache=False)(["python", "check.py"], {"check.py": "print(1)", "../escaped.txt": "x"})
    assert out.exit != 0 and "outside the workspace" in out.stderr


def test_safehub_rejects_bundles_whose_files_escape_the_workspace(gw):
    hub = safe_hub(gw)
    b = honest_bundle(gw)
    b.post_state = {**b.post_state, "../evomap_traversal_marker.txt": "x"}
    reasons, _ = hub.static_gate(b)
    assert any(r.startswith("path guard: post_state") for r in reasons)
    assert hub.publish(honest_bundle(gw, author="bob"), "bob").status == "verified"  # honest bundles still pass


def test_safehub_isolates_real_model_code_by_default(gw):
    assert not _is_real_model(MockLLM())
    assert _is_real_model(ClaudeCLI("haiku")) and _is_real_model(CachedLLM(ClaudeCLI("haiku"), "/tmp/rsi-unused"))
    assert isinstance(safe_hub(gw).runner.executor, InProcessExecutor)  # simulated worlds keep the fast executor
