"""Shared fixtures for the GEPA tests."""
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from rsi.domains.agentqa import AgentQADomain, SimModel, make_suite
from rsi.domains.ruleworld import RuleWorldReflectionLM, make_domain
from rsi.gepa import AgentQAReflectionLM, Config, FrontierTracker, run, two_module_harness

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "results" / "gepa"


def _split(kw: dict) -> tuple[dict, dict]:
    """Split keyword arguments into ``Config`` fields and ``run`` arguments."""
    cfg = {k: v for k, v in kw.items() if k in Config.__dataclass_fields__}
    return cfg, {k: v for k, v in kw.items() if k not in cfg}


@pytest.fixture
def ruleworld_run():
    """GEPA on ``make_domain(seed)`` with the mock reflection LM; returns ``(domain, result)``."""
    def go(seed: int = 0, B: int = 600, **kw):
        d = make_domain(seed=seed)
        cfg, other = _split(kw)
        return d, run(d, d.seed_artifact(), llm_propose=RuleWorldReflectionLM(d.world),
                      config=Config(max_metric_calls=B, seed=seed, **cfg), **other)
    return go


@pytest.fixture
def merge_run(ruleworld_run):
    """GEPA+Merge on RuleWorld at B = 1500."""
    return lambda seed=0, **kw: ruleworld_run(seed, 1500, use_merge=True, **kw)[1]


@pytest.fixture
def agentqa_run():
    """GEPA on a small AgentQA suite with the two-module harness; ``task_llm`` builds the task LM from the suite."""
    def go(task_llm=SimModel, n_holdout: int = 6, n_ood_per_family: int = 2, **kw):
        suite = make_suite(n_evolve=6, n_val=6, n_holdout=n_holdout, n_ood_per_family=n_ood_per_family, seed=0)
        cfg, other = _split(kw)
        return run(AgentQADomain(suite), two_module_harness(), llm_task=task_llm(suite),
                   llm_propose=AgentQAReflectionLM(), config=Config(**cfg), **other)
    return go


@pytest.fixture
def result():
    """Load a recorded ``results/gepa`` file: the local copy, else the ``data`` branch, else skip."""
    def load(name: str) -> dict:
        p = RESULTS / name
        if p.exists():
            return json.loads(p.read_text())
        for ref in ("data", "origin/data"):
            r = subprocess.run(["git", "-C", str(ROOT), "show", f"{ref}:results/gepa/{name}"], capture_output=True, text=True)
            if r.returncode == 0:
                return json.loads(r.stdout)
        pytest.skip(f"{p} not generated and no data branch")
    return load


@pytest.fixture
def selector_state():
    """A minimal search state for selectors: aggregate scores and an instance frontier."""
    def make(agg: list[float], front: dict) -> SimpleNamespace:
        frontier = FrontierTracker("instance")
        frontier.progs = {k: set(v) for k, v in front.items()}
        return SimpleNamespace(candidates=list(range(len(agg))), frontier=frontier, agg_scores=lambda: agg)
    return make
