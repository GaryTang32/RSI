"""Integrator hardening: forged sandbox results, infra error propagation, ledger truthiness."""
from __future__ import annotations

from rsi.core import (Artifact, CachedLLM, Domain, Evaluator, Ledger, LLMResponse, MockLLM, Task, TaskSuite, Usage,
                      call_function)

ATEXIT_FORGER = '''
import atexit, sys
def f(x):
    print("__RSI_RESULT__99")
    atexit.register(lambda: sys.stdout.write("\\n__RSI_RESULT__123\\n"))
    return x
'''

EXIT_FORGER = '''
import os
def f(x):
    print("__RSI_RESULT__777")
    os._exit(0)
'''


def test_call_function_normal():
    assert call_function("def f(x): return x * 2", "f", {"x": 21})[0] == 42


def test_call_function_ignores_forged_markers():
    assert call_function(ATEXIT_FORGER, "f", {"x": 5})[0] == 5
    assert call_function(EXIT_FORGER, "f", {"x": 5})[0] is None


class _InfraDomain(Domain):
    def execute(self, artifact, task, *, seed, llm):
        raise RuntimeError("infra: backend down")

    def grade(self, task, execution):
        return 1.0, ""


def test_infra_errors_become_missing_trials_not_cached():
    ev = Evaluator(_InfraDomain(TaskSuite([Task("a", 1)], {"evolve": ["a"]})), None, workers=1)
    r = ev.evaluate(Artifact({"x": "1"}), "evolve", k=2)
    assert r.n_missing == 2 and r.score == 0.0 and r.trials["a"][0].error.startswith("infra:")


def test_empty_ledger_is_truthy():
    led = Ledger()
    assert len(led) == 0 and bool(led) is True and (led or None) is led


def test_cached_llm_keeps_stop_reason(tmp_path):
    class Truncating(MockLLM):
        def _complete(self, prompt, *, system, max_tokens, seed):
            return LLMResponse(text="partial", usage=Usage(1, 1, 1, 0.0, 0.0), model="m",
                               raw={"stop_reason": "max_tokens"})

    c = CachedLLM(Truncating(), tmp_path)
    first, again = c.complete("p"), c.complete("p")
    assert first.raw["stop_reason"] == "max_tokens"
    assert again.raw["cached"] is True and again.raw["stop_reason"] == "max_tokens"
