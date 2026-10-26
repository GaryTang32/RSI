"""Shared fixtures for the Harness² tests: a small AgentQA stream, a recording domain and recording improvers."""
from __future__ import annotations

import pytest

from harness2_helpers import RecordingDomain, RecordingImprover

from rsi.domains.agentqa import SimModel, make_suite
from rsi.harness2 import Config, run


@pytest.fixture(scope="session")
def suite():
    return make_suite(n_evolve=2, n_holdout=4, n_ood_per_family=1)


@pytest.fixture
def dom(suite):
    return RecordingDomain(suite)


@pytest.fixture
def sim(suite):
    return SimModel(suite)


@pytest.fixture
def go(dom, sim, tmp_path):
    """``go(**config)`` -> (result, improver); extra keys ``improver=`` / ``out=`` / ``budget=`` / ``tasks=``."""
    def _go(improver=None, out="run", budget=None, tasks=None, **cfg):
        imp = improver or RecordingImprover()
        cfg.setdefault("split", "holdout+ood")
        res = run(dom, dom.seed_artifact(), llm_task=sim, llm_propose=imp, config=Config(**cfg),
                  out_dir=tmp_path / out if out else None, budget=budget, tasks=tasks)
        return res, imp
    return _go
