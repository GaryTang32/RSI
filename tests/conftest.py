"""Shared pytest setup: an importable checkout, per-test isolation of experiment modules, a fresh-interpreter runner."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_EXPERIMENTS = str(ROOT / "experiments")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _drop_experiment_modules() -> None:
    for name, mod in list(sys.modules.items()):
        if (getattr(mod, "__file__", None) or "").startswith(_EXPERIMENTS):
            del sys.modules[name]


@pytest.fixture(autouse=True)
def _isolate_experiment_modules():
    """Experiment folders share helper module names (``_common``): no test may see another one's cached copy."""
    saved_path = list(sys.path)
    _drop_experiment_modules()
    yield
    sys.path[:] = saved_path
    _drop_experiment_modules()


@pytest.fixture
def checkout_python():
    """Run code in a fresh interpreter that imports this checkout's ``rsi``, whatever the working directory."""
    def run(code: str, *, env: dict | None = None, cwd: Path | str = ROOT) -> subprocess.CompletedProcess:
        path = os.pathsep.join(p for p in (str(ROOT), os.environ.get("PYTHONPATH")) if p)
        return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60, cwd=cwd,
                              env={**os.environ, "PYTHONPATH": path, **(env or {})})
    return run
