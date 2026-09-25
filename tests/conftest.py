"""Shared pytest setup for the whole test tree.

Deliberately minimal so it cannot interfere with any engineer's test module:
it only makes the repository importable without installation and (re)registers
the two markers already declared in ``pyproject.toml``. No fixtures, no
autouse hooks, no command-line options.

Live tests (``@pytest.mark.live``) are deselected by the ``-m 'not live'``
default in ``pyproject.toml``; run them with ``pytest -m live``.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def pytest_configure(config):
    # idempotent: duplicates of the pyproject markers are harmless
    config.addinivalue_line("markers", "live: needs a real LLM backend (claude CLI or API key); skipped by default")
    config.addinivalue_line("markers", "slow: takes more than a few seconds")
