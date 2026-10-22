"""Shared fixtures for the Dream-RSI tests."""
import importlib
from pathlib import Path

import pytest

from rsi.domains.agentqa import AgentQADomain, SimModel, make_suite
from rsi.domains.discovery import SyntheticConfig, SyntheticDomain
from rsi.domains.discovery.agentqa_agent import MECHANISMS, agentqa_mock_agent
from rsi.dream import Config, DomainTask, run

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def synthetic_world():
    """Factory: the world recorded by one fixed-policy round on the synthetic domain of ``world_seed``."""
    def make(world_seed: int, **cfg):
        config = Config(**{"rounds": 1, "W": 4, "dream": False, "sandbox": "inprocess", **cfg})
        return run(SyntheticDomain(SyntheticConfig(seed=world_seed)), config=config).meta["worlds"][0]
    return make


@pytest.fixture
def agentqa_run():
    """Factory: an offline run on AgentQA through ``DomainTask`` with the mock agent."""
    def go(n_ood_per_family: int, out_dir=None, **cfg):
        suite = make_suite(n_evolve=6, n_holdout=6, n_ood_per_family=n_ood_per_family, seed=1)
        dom, sim = AgentQADomain(suite), SimModel(suite)
        task = DomainTask(dom, dom.seed_artifact(), sim, directions=MECHANISMS, workers=2)
        config = Config(**{"W": 2, "branch_count": 3, "refine_count": 1, "sandbox": "inprocess", **cfg})
        return run(task, llm_task=sim, agent=agentqa_mock_agent(), config=config, out_dir=out_dir)
    return go


@pytest.fixture
def dream_script(monkeypatch):
    """Importer for the scripts in experiments/dream-rsi."""
    monkeypatch.syspath_prepend(str(ROOT / "experiments" / "dream-rsi"))
    return importlib.import_module
