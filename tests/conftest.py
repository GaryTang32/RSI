"""Shared pytest setup for the whole test tree.

Deliberately minimal so it cannot interfere with any engineer's test module:
it makes the repository importable without installation, (re)registers the two
markers already declared in ``pyproject.toml``, and has one autouse fixture that
keeps experiment scripts from leaking between tests. Several ``experiments/<slug>/``
directories ship a helper module with the same name (``_common``), so a test that
imports one method's experiment script must not see another method's cached copy.

Live tests (``@pytest.mark.live``) are deselected by the ``-m 'not live'``
default in ``pyproject.toml``; run them with ``pytest -m live``.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def pytest_configure(config):
    # idempotent: duplicates of the pyproject markers are harmless
    config.addinivalue_line("markers", "live: needs a real LLM backend (claude CLI or API key); skipped by default")
    config.addinivalue_line("markers", "slow: takes more than a few seconds")


_EXPERIMENTS = str(ROOT / "experiments")


def _drop_experiment_modules() -> None:
    for name, mod in list(sys.modules.items()):
        if (getattr(mod, "__file__", None) or "").startswith(_EXPERIMENTS):
            del sys.modules[name]


@pytest.fixture(autouse=True)
def _isolate_experiment_modules():
    """Give each test a clean view of experiment modules and of sys.path."""
    saved_path = list(sys.path)
    _drop_experiment_modules()
    yield
    sys.path[:] = saved_path
    _drop_experiment_modules()
