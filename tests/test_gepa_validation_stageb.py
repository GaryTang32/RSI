"""Stage-B audit regressions for GEPA.

* a sustained backend outage stops the run (``infra_outage``) instead of charging b rollouts per
  iteration until the rollout budget is gone (the interrupted live validation run kept looping);
* the stopper is a safety net only: it never fires on ordinary runs and never replaces a stop condition;
* the independent stage-B replay (reference sampler, re-implemented Alg. 2 / merge, reference prompt
  renderer and parser) reproduces every step of a fresh RuleWorld+merge run.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from rsi.core.llm import LLM, LLMResponse, Usage
from rsi.domains.agentqa import AgentQADomain, SimModel, make_suite
from rsi.domains.ruleworld import RuleWorldReflectionLM, make_domain
from rsi.gepa import AgentQAReflectionLM, Config, ConsecutiveInfraFailures, run, two_module_harness

ROOT = Path(__file__).resolve().parents[1]


class OutageLLM(LLM):
    """The offline task model, until its backend goes down after ``ok_calls`` calls."""

    def __init__(self, inner: LLM, ok_calls: int) -> None:
        super().__init__()
        self.inner, self.ok_calls, self.n = inner, ok_calls, 0
        self.name = inner.name

    def _complete(self, prompt, *, system, max_tokens, seed) -> LLMResponse:
        self.n += 1
        if self.n > self.ok_calls:
            return LLMResponse(text="", usage=Usage(), model=self.name, error="backend down (usage limit)")
        return self.inner.complete(prompt, system=system, max_tokens=max_tokens, seed=seed)


def _agentqa(llm_task, **cfg):
    suite = make_suite(n_evolve=6, n_val=6, n_holdout=3, n_ood_per_family=1, seed=0)
    dom = AgentQADomain(suite)
    if callable(llm_task):
        llm_task = llm_task(suite)
    return run(dom, two_module_harness(), llm_task=llm_task, llm_propose=AgentQAReflectionLM(),
               config=Config(**{"max_metric_calls": 400, "seed": 0, **cfg}))


def test_sustained_outage_stops_the_run():
    res = _agentqa(lambda s: OutageLLM(SimModel(s), ok_calls=60))
    events = [e.get("event") for e in res.state.trace]
    assert res.stop_reason == "infra_outage"
    assert events[-3:] == ["skip_infra_error"] * 3
    assert res.meta["rollouts"] < 400                  # the rest of the budget is not burnt on a dead backend
    # before the fix the loop charged 3 rollouts per dead iteration up to max_metric_calls
    old = _agentqa(lambda s: OutageLLM(SimModel(s), ok_calls=60), max_consecutive_infra_failures=None)
    assert old.stop_reason == "max_metric_calls" and old.meta["rollouts"] >= 400
    assert sum(e.get("event") == "skip_infra_error" for e in old.state.trace) > 10


def test_outage_stopper_is_only_a_safety_net():
    a = _agentqa(SimModel, max_metric_calls=120)
    b = _agentqa(SimModel, max_metric_calls=120, max_consecutive_infra_failures=None)
    assert a.stop_reason == b.stop_reason == "max_metric_calls"
    assert a.trajectory == b.trajectory and a.best.id == b.best.id
    # it never counts as the run's stop condition on its own
    with pytest.raises(ValueError):
        _agentqa(SimModel, max_metric_calls=None)


def test_outage_stopper_counts_reflection_llm_errors():
    class Eng:
        class state:
            trace = [{"event": "accepted"}, {"event": "no_proposal", "rejected_outputs": {"a": "llm error: limit"}},
                     {"event": "skip_infra_error"}, {"event": "skip_infra_error"}]
    assert ConsecutiveInfraFailures(3)(Eng) == "infra_outage"
    Eng.state.trace[1] = {"event": "no_proposal", "rejected_outputs": {"a": "unparseable"}}
    assert ConsecutiveInfraFailures(3)(Eng) is None
    assert ConsecutiveInfraFailures(2)(Eng) == "infra_outage"


def _stageb():
    p = ROOT / "experiments" / "gepa" / "validate_gepa_stageb.py"
    spec = importlib.util.spec_from_file_location("validate_gepa_stageb", p)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["validate_gepa_stageb"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_stageb_replay_reproduces_every_step(tmp_path):
    sb = _stageb()
    if not sb.HAVE_REF:
        pytest.skip("reference gepa-ai/gepa sources not available")
    d = make_domain(seed=0, feedback="rich")
    run(d, d.seed_artifact(), llm_propose=RuleWorldReflectionLM(d.world),
        config=Config(max_metric_calls=700, seed=0, use_merge=True), out_dir=tmp_path / "rw")
    a = sb.audit("ruleworld_merge_offline", tmp_path / "rw")
    bad = {k: v for k, v in a["checks"].items() if v.split("/")[0] != v.split("/")[1]}
    assert not bad, bad
    assert all(v is True for k, v in a["final"].items() if isinstance(v, bool))
    assert any(s.get("kind") == "merge" for s in a["steps"])
    assert int(a["checks"]["prompt_equals_reference_render"].split("/")[1]) > 5
