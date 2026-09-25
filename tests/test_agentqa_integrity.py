"""AgentQA harness isolation (second core-qa review round).

* a harness that never returns used to hang the whole Evaluator forever; it is now
  stopped after ``harness_timeout_s`` of its own time (time inside model and tool
  calls does not count) and graded as a failure;
* a harness could monkeypatch the grader (``is_correct``, builtins, ...), scoring
  1.0 itself and corrupting every later trial in the process; it is now graded
  ``tamper: ...`` and the grading path is restored;
* the harness thread no longer has the Task (whose target is the answer) in its
  caller's frame;
* ``decontaminate`` keeps custom sealed splits sealed.
"""
from __future__ import annotations

import threading
import time

import pytest

from rsi.core import Artifact, Evaluator, Execution, MockLLM, Task, TaskSuite
from rsi.domains.agentqa import AgentQADomain, SimModel, decontaminate, is_correct, make_suite
from rsi.domains.agentqa import domain as aqd


@pytest.fixture
def dom():
    d = AgentQADomain(make_suite(n_evolve=2, n_holdout=1, n_ood_per_family=1))
    d.harness_timeout_s = 0.5
    return d


@pytest.fixture
def sim(dom):
    return SimModel(dom.tasks)


def H(body: str, top: str = "") -> Artifact:
    return Artifact({"harness.py": top + "def solve(question, llm, tools, files):\n" + body,
                     "prompts/task.md": "{question}\nReply with ANSWER: <value>.\n"})


def _t(dom):
    return dom.tasks.split("evolve")[0]


def _harness_threads():
    return [t for t in threading.enumerate() if t.name == "agentqa-harness"]


# ------------------------------------------------------------------ watchdog
@pytest.mark.parametrize("art", [
    H("    while True:\n        pass\n"),
    H("    while True:\n        try:\n            x = 1\n        except BaseException:\n            pass\n"),
    H("    return 'x'\n", top="while True:\n    pass\n"),                   # loops at import time
])
def test_non_terminating_harness_is_stopped_and_graded(dom, sim, art):
    t0 = time.time()
    ev = Evaluator(dom, sim, workers=2).evaluate(art, "evolve", k=1)
    assert time.time() - t0 < 10
    assert ev.score == 0.0 and ev.n_missing == 0
    assert all(t.error.startswith("HarnessTimeout") for trs in ev.trials.values() for t in trs)
    deadline = time.time() + 20
    while _harness_threads() and time.time() < deadline:            # the reaper ends the spinning threads
        time.sleep(0.1)
    assert not _harness_threads()


def test_time_inside_model_and_tool_calls_is_not_counted(dom):
    slow = MockLLM(lambda p, s, seed, i: (time.sleep(0.25), "ANSWER: 1")[1], name="slow")
    art = H("    for _ in range(3):\n        r = llm(question)\n"
            "    out = tools.python('import time; time.sleep(0.6); print(5)')\n    return r + out\n")
    tr = dom.run(art, _t(dom), llm=slow)                              # 0.75 s in the model + 0.6 s in the tool
    assert tr.error is None and tr.meta == {"llm_calls": 3, "tool_calls": 1}
    tr = dom.run(H("    import time\n    time.sleep(0.2)\n    return 'ANSWER: 1'\n"), _t(dom), llm=slow)
    assert tr.error is None                                           # own time under the budget is fine


def test_timed_out_harness_can_no_longer_call_the_model(dom):
    calls = []
    llm = MockLLM(lambda p, s, seed, i: calls.append(i) or "ANSWER: 1", name="m")
    # time.sleep is a C call: the harness is abandoned at the budget and stopped when it wakes up
    tr = dom.run(H("    import time\n    time.sleep(1.0)\n    return llm(question)\n"), _t(dom), llm=llm)
    assert tr.error.startswith("HarnessTimeout")
    time.sleep(1.5)
    assert calls == [] and not _harness_threads()


def test_harness_frame_does_not_hold_the_task(dom, sim):
    art = H("    import sys\n    f = sys._getframe(1)\n    return repr(sorted(f.f_locals))\n")
    tr = dom.run(art, _t(dom), llm=sim)
    assert tr.error is None and "'task'" not in tr.output and "'question'" in tr.output


def test_default_budget_and_normal_harnesses_unchanged(sim):
    d = AgentQADomain(make_suite(n_evolve=4, n_holdout=1, n_ood_per_family=1))
    assert d.harness_timeout_s == aqd.HARNESS_TIMEOUT_S == 60.0
    seed = AgentQADomain.seed_artifact()
    s = SimModel(d.tasks)
    a = Evaluator(d, s, workers=4).evaluate(seed, "evolve", k=3)
    b = Evaluator(d, s, workers=1).evaluate(seed, "evolve", k=3)
    assert [t.output for trs in a.trials.values() for t in trs] == [t.output for trs in b.trials.values() for t in trs]
    assert a.error_rate == 0.0


# ------------------------------------------------------------------ grading-path tripwire
EVIL = [
    ("import rsi.domains.agentqa.domain as d\nd.is_correct = lambda *a: True\n", "is_correct"),
    ("from rsi.domains.agentqa.domain import AgentQADomain\nAgentQADomain.grade = lambda self, t, e: (1.0, '')\n",
     "AgentQADomain.grade"),
    ("import builtins\nbuiltins.float = lambda *a: 0.0\n", "builtins.float"),
    ("import rsi.core.evaluate as e\ne.EvalResult.task_scores = lambda self: {k: 1.0 for k in self.trials}\n",
     "EvalResult.task_scores"),
]


@pytest.mark.parametrize("patch, name", EVIL)
def test_grader_tampering_is_caught_and_undone(dom, sim, patch, name):
    orig = {n: getattr(o, n) for o, n, _ in aqd._INTEGRITY}
    evil = H("    return 'ANSWER: wrong'\n", top=patch)
    ev = Evaluator(dom, sim, workers=1).evaluate(evil, "evolve", k=2)
    assert ev.score == 0.0
    errs = [t.error for trs in ev.trials.values() for t in trs]
    assert all(e.startswith("tamper:") and name in e for e in errs), errs
    assert {n: getattr(o, n) for o, n, _ in aqd._INTEGRITY} == orig   # everything restored
    honest = Evaluator(dom, sim, workers=1).evaluate(H("    return 'ANSWER: wrong'\n"), "evolve", k=2)
    assert honest.score == 0.0 and honest.error_rate == 0.0          # later trials are graded honestly


def test_grade_restores_a_concurrently_patched_grader(dom, monkeypatch):
    t = _t(dom)
    monkeypatch.setattr(aqd, "is_correct", lambda *a: True)          # as if another harness thread did it
    assert dom.grade(t, Execution(output="ANSWER: definitely wrong"))[0] == 0.0
    assert aqd.is_correct is is_correct


# ------------------------------------------------------------------ suites
def test_decontaminate_keeps_custom_sealed_splits_sealed():
    s = make_suite(n_evolve=4, n_holdout=2, n_ood_per_family=1)
    s.splits["audit"] = list(s.splits["holdout"])
    s.seal("audit")
    s.unseal("ood")
    d = decontaminate(s)
    assert d.is_sealed("audit") and d.is_sealed("holdout") and not d.is_sealed("ood")
    assert not d.is_sealed("evolve")
