"""Noise-band defaults: RRSI corrects its small-k bootstrap; GEPA and autoresearch warn when no noise band is set."""
import warnings
from types import SimpleNamespace

import pytest

import rsi.autoresearch.loop as ar_loop
import rsi.gepa.api as gepa_api
from rsi.core import Artifact, FunctionDomain, Task, TaskSuite
from rsi.gepa import Config as GepaConfig
from rsi.rrsi.config import PRESETS, Config as RRSIConfig


def test_rrsi_calibration_applies_the_small_k_correction_by_default():
    assert RRSIConfig().bootstrap_small_k_correction is True
    assert all(PRESETS[p]["delta"] for p in ("coding", "workspace", "eng"))   # the paper presets fix delta


def _domain():
    tasks = [Task(f"t{i}", i, i) for i in range(4)]
    return FunctionDomain(TaskSuite(tasks, {"evolve": ["t0", "t1"], "val": ["t2", "t3"]}),
                          lambda a, t, seed, llm: None, lambda t, e: (0.0, ""))


@pytest.fixture
def no_gepa_search(monkeypatch):
    monkeypatch.setattr(gepa_api, "GEPAEngine", lambda *a, **k: SimpleNamespace(run=lambda: None))
    monkeypatch.setattr(gepa_api, "build_result", lambda eng, method: SimpleNamespace(meta={}, baseline=None, best=None))


def test_gepa_warns_without_a_noise_margin(no_gepa_search):
    with pytest.warns(UserWarning, match="no noise band"):
        gepa_api.run(_domain(), Artifact({"p.md": "x"}), components=["p.md"])
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        gepa_api.run(_domain(), Artifact({"p.md": "x"}), components=["p.md"],
                     config=GepaConfig(acceptance="noise_margin", noise_margin=0.05))


@pytest.mark.parametrize("rule,warns", [("upstream", True), ("strict", True), ("simplicity", True), ("rigor", False)])
def test_autoresearch_warns_for_keep_rules_without_a_noise_band(monkeypatch, rule, warns):
    class Loop:
        def __init__(self, *a, **k):
            self.keep = SimpleNamespace(name=rule)

        def run(self):
            return "ran"

    monkeypatch.setattr(ar_loop, "AutoresearchLoop", Loop)
    task = SimpleNamespace()
    if warns:
        with pytest.warns(UserWarning, match="keeps any improvement on one run"):
            assert ar_loop.run(task, agent=SimpleNamespace()) == "ran"
    else:
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            assert ar_loop.run(task, agent=SimpleNamespace()) == "ran"
