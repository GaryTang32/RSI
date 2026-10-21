"""Stage-B audit regressions for GEPA: the infra-outage stopper and the independent stage-B replay."""
import functools
from pathlib import Path

import pytest

from rsi.core.llm import LLM, LLMResponse, Usage
from rsi.domains.agentqa import SimModel
from rsi.domains.ruleworld import RuleWorldReflectionLM, make_domain
from rsi.gepa import Config, ConsecutiveInfraFailures, run

EXPERIMENTS = Path(__file__).resolve().parents[2] / "experiments" / "gepa"


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


def _outage(suite) -> OutageLLM:
    return OutageLLM(SimModel(suite), ok_calls=60)


@pytest.fixture
def agentqa(agentqa_run):
    return functools.partial(agentqa_run, n_holdout=3, n_ood_per_family=1, max_metric_calls=400)


def test_sustained_outage_stops_the_run(agentqa):
    res = agentqa(_outage)
    assert res.stop_reason == "infra_outage"
    assert [e.get("event") for e in res.state.trace][-3:] == ["skip_infra_error"] * 3
    assert res.meta["rollouts"] < 400                  # the rest of the budget is not burnt on a dead backend
    old = agentqa(_outage, max_consecutive_infra_failures=None)
    assert old.stop_reason == "max_metric_calls" and old.meta["rollouts"] >= 400
    assert sum(e.get("event") == "skip_infra_error" for e in old.state.trace) > 10


def test_outage_stopper_is_only_a_safety_net(agentqa):
    a = agentqa(max_metric_calls=120)
    b = agentqa(max_metric_calls=120, max_consecutive_infra_failures=None)
    assert a.stop_reason == b.stop_reason == "max_metric_calls"
    assert a.trajectory == b.trajectory and a.best.id == b.best.id
    with pytest.raises(ValueError):                    # it never counts as the run's stop condition
        agentqa(max_metric_calls=None)


def test_outage_stopper_counts_reflection_llm_errors():
    class Eng:
        class state:
            trace = [{"event": "accepted"}, {"event": "no_proposal", "rejected_outputs": {"a": "llm error: limit"}},
                     {"event": "skip_infra_error"}, {"event": "skip_infra_error"}]

    assert ConsecutiveInfraFailures(3)(Eng) == "infra_outage"
    Eng.state.trace[1] = {"event": "no_proposal", "rejected_outputs": {"a": "unparseable"}}
    assert ConsecutiveInfraFailures(3)(Eng) is None
    assert ConsecutiveInfraFailures(2)(Eng) == "infra_outage"


def test_stageb_replay_reproduces_every_step(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(EXPERIMENTS))
    import validate_gepa_stageb as sb
    if not sb.HAVE_REF:
        pytest.skip("reference gepa-ai/gepa sources not available")
    d = make_domain(seed=0, feedback="rich")
    run(d, d.seed_artifact(), llm_propose=RuleWorldReflectionLM(d.world),
        config=Config(max_metric_calls=700, seed=0, use_merge=True), out_dir=tmp_path / "rw")
    a = sb.audit("ruleworld_merge_offline", tmp_path / "rw")
    bad = {k: v for k, v in a["checks"].items() if v.split("/")[0] != v.split("/")[1]}
    assert not bad, bad
    assert all(v is True for v in a["final"].values() if isinstance(v, bool))
    assert any(s.get("kind") == "merge" for s in a["steps"])
    assert int(a["checks"]["prompt_equals_reference_render"].split("/")[1]) > 5
