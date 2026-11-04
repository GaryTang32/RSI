"""Regression tests for the core findings of the November 2026 code review (spend cap, splits, caches)."""

from __future__ import annotations

import sys
import types
import warnings

import numpy as np
import pytest

from rsi.core import Artifact, Evaluator, FunctionDomain, MockLLM, Task, TaskSuite
from rsi.core.llm import CachedLLM, ClaudeCLI, LLMResponse, Usage, price_per_mtok


def _fake_anthropic(monkeypatch, stop_reason="end_turn"):
    """An ``anthropic`` module whose client answers every call with 1M input and 1M output tokens."""

    class _Messages:
        def create(self, **kw):
            return types.SimpleNamespace(
                content=[types.SimpleNamespace(text="hi")],
                usage=types.SimpleNamespace(input_tokens=1_000_000, output_tokens=1_000_000),
                stop_reason=stop_reason,
            )

    class _Client:
        messages = _Messages()

    monkeypatch.setitem(sys.modules, "anthropic", types.SimpleNamespace(Anthropic=_Client))
    from rsi.core.llm import AnthropicAPI

    return AnthropicAPI


# H1: every model alias is priced, so max_usd caps work beyond Haiku


@pytest.mark.parametrize("alias", ["haiku", "sonnet", "opus", "fable"])
def test_every_alias_has_a_price(alias):
    assert price_per_mtok(alias) is not None


@pytest.mark.parametrize("alias,expected", [("opus", 24.0), ("sonnet", 12.0), ("haiku", 6.0)])
def test_api_backend_meters_dollars_for_every_model(monkeypatch, alias, expected):
    AnthropicAPI = _fake_anthropic(monkeypatch)
    llm = AnthropicAPI(alias)
    resp = llm.complete("q")
    assert resp.usage.cost_usd == pytest.approx(expected)  # 1M in + 1M out at that model's rates
    assert llm.meter.total().cost_usd == pytest.approx(expected)


def test_api_backend_warns_when_a_model_has_no_price(monkeypatch):
    AnthropicAPI = _fake_anthropic(monkeypatch)
    with pytest.warns(UserWarning, match="max_usd"):
        llm = AnthropicAPI("claude-unknown-9")
    assert llm.complete("q").usage.cost_usd == 0.0


# L3: the API backend reports why a reply stopped, and the cache keeps it


def test_api_backend_keeps_stop_reason_through_the_cache(monkeypatch, tmp_path):
    AnthropicAPI = _fake_anthropic(monkeypatch, stop_reason="max_tokens")
    llm = CachedLLM(AnthropicAPI("haiku"), tmp_path)
    assert llm.complete("q").raw["stop_reason"] == "max_tokens"
    replay = llm.complete("q")
    assert replay.raw["cached"] and replay.raw["stop_reason"] == "max_tokens"


# M2: backend settings that change replies are part of the cache key


def test_cli_effort_and_api_max_tokens_change_the_cache_key(monkeypatch, tmp_path):
    assert ClaudeCLI("haiku").name == "claude-cli:claude-haiku-4-5-20251001"  # unset: the old name, old caches
    assert ClaudeCLI("haiku", effort="low").name != ClaudeCLI("haiku", effort="high").name
    low = CachedLLM(ClaudeCLI("haiku", effort="low"), tmp_path)
    high = CachedLLM(ClaudeCLI("haiku", effort="high"), tmp_path)
    assert low._key("p", None, None, 0) != high._key("p", None, None, 0)
    AnthropicAPI = _fake_anthropic(monkeypatch)
    assert AnthropicAPI("haiku").name == "api:claude-haiku-4-5-20251001"
    assert AnthropicAPI("haiku", max_tokens=256).name != AnthropicAPI("haiku").name


# H2: duplicate task ids no longer move sealed tasks into the loop's split


def test_duplicate_task_ids_are_rejected():
    tasks = [Task(f"q{i}", f"{fam} {i}", "x", family=fam) for fam in ("easy", "hard") for i in range(3)]
    with pytest.raises(ValueError, match="unique"):
        TaskSuite.from_tasks(tasks, fractions={"evolve": 0.5, "holdout": 0.5}, by_family={"ood": ["hard"]})


def test_unique_ids_keep_whole_families_sealed():
    tasks = [Task(f"{fam}-{i}", f"{fam} {i}", "x", family=fam) for fam in ("easy", "hard") for i in range(3)]
    suite = TaskSuite.from_tasks(tasks, fractions={"evolve": 0.5, "holdout": 0.5}, by_family={"ood": ["hard"]})
    assert {t.family for t in suite.split("evolve")} == {"easy"}
    assert {t.family for t in suite.split("ood", allow_sealed=True)} == {"hard"}


def test_an_explicit_overlap_between_decision_and_sealed_splits_warns():
    tasks = [Task(f"t{i}", i, i) for i in range(3)]
    with pytest.warns(UserWarning, match="not held out"):
        TaskSuite(tasks, {"evolve": ["t0", "t1"], "holdout": ["t1", "t2"]})
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        TaskSuite(tasks, {"evolve": ["t0"], "holdout": ["t1", "t2"], "smoke": ["t0"]})  # smoke is not sealed


# M1: a failed model call is missing and retried, not a cached wrong answer


class _FailingLLM(MockLLM):
    def __init__(self):
        super().__init__(name="down")
        self.fail = True

    def _complete(self, prompt, *, system, max_tokens, seed):
        if self.fail:
            return LLMResponse(text="", usage=Usage(1), model=self.name, error="connection refused")
        return LLMResponse(text="4", usage=Usage(1), model=self.name)


def _arith_domain(**kw):
    tasks = [Task("a", "2+2", "4"), Task("b", "1+3", "4")]
    suite = TaskSuite(tasks, {"evolve": ["a", "b"]})
    return FunctionDomain(
        suite,
        execute_fn=lambda art, task, seed, llm: llm.complete(task.input, seed=seed).text,
        grade_fn=lambda task, out: float(out.strip() == task.target),
        **kw,
    )


def test_failed_model_calls_are_infra_errors_that_are_never_cached(tmp_path):
    llm = _FailingLLM()
    ev = Evaluator(_arith_domain(), llm, cache_dir=tmp_path)
    art = Artifact({"p.md": "x"})
    r = ev.evaluate(art, "evolve")
    assert r.n_missing == 2 and all(t.error.startswith("infra:") for trs in r.trials.values() for t in trs)
    llm.fail = False  # the backend comes back: the trials run fresh instead of replaying cached zeros
    r2 = Evaluator(_arith_domain(), llm, cache_dir=tmp_path).evaluate(art, "evolve")
    assert r2.n_missing == 0 and r2.score == 1.0


def test_the_wrapped_model_is_still_an_llm_and_meters_on_the_original():
    from rsi.core import LLM

    seen = {}

    def execute(art, task, seed, llm):
        seen["is_llm"], seen["name"] = isinstance(llm, LLM), llm.name
        return llm.complete(task.input, seed=seed).text

    inner = _FailingLLM()
    inner.fail = False
    dom = _arith_domain()
    dom.execute_fn = execute
    Evaluator(dom, inner).evaluate(Artifact({"p.md": "x"}), "evolve")
    assert seen == {"is_llm": True, "name": "down"} and inner.meter.total().calls == 2


def test_raise_on_llm_error_false_keeps_the_reply_for_the_caller():
    llm = _FailingLLM()
    r = Evaluator(_arith_domain(raise_on_llm_error=False), llm).evaluate(Artifact({"p.md": "x"}), "evolve")
    assert r.n_missing == 0 and r.score == 0.0


# M3: the trial cache key covers everything the grader can read


def _ident(task):
    return Evaluator(_arith_domain(), MockLLM(), cache_dir="unused").cache_identity(task)


def test_trial_cache_identity_covers_meta_arrays_and_colliding_ids():
    assert _ident(Task("q", "in", "t", meta={"answer": "A"})) != _ident(Task("q", "in", "t", meta={"answer": "B"}))
    assert _ident(Task("q", "in", "t")) == _ident(Task("q", "in", "t", meta={}))  # no meta: the old identity
    a, b = np.zeros(5000), np.zeros(5000)
    b[2500] = 1.0  # same truncated repr, different data
    assert repr(a) == repr(b)
    assert _ident(Task("q", a)) != _ident(Task("q", b))
    assert _ident(Task("q/1", "in")) != _ident(Task("q_1", "in"))  # these share a cache file name
