"""SoL-Pi refuses to run with its held-out firewall on but no held-out split (it used to switch the firewall off)."""
import pytest

from rsi.core import Artifact, FunctionDomain, Task, TaskSuite
from rsi.solpi import Idea
from rsi.solpi.driver import AutoResearchDriver, Config


def _domain(splits: dict) -> FunctionDomain:
    tasks = [Task(f"t{i}", i, i, family="f") for i in range(4)]
    return FunctionDomain(TaskSuite(tasks, splits), lambda a, t, seed, llm: None, lambda t, e: (0.0, ""))


def _driver(dom, **cfg):
    return AutoResearchDriver(dom, Artifact({"p.md": "x"}), llm_task=None, ideas=[Idea("i", "C", "t", "m")],
                              proposer=object(), config=Config(**cfg))


@pytest.mark.parametrize("splits", [{"evolve": ["t0", "t1", "t2", "t3"]},
                                    {"evolve": ["t0", "t1", "t2", "t3"], "holdout": []}])
def test_missing_or_empty_holdout_split_is_an_error(splits):
    with pytest.raises(ValueError, match="held-out firewall needs a non-empty 'holdout' split"):
        _driver(_domain(splits))


def test_firewall_false_or_a_holdout_split_runs():
    _driver(_domain({"evolve": ["t0", "t1", "t2", "t3"]}), firewall=False)
    _driver(_domain({"evolve": ["t0", "t1"], "holdout": ["t2", "t3"]}))
