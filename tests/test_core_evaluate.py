"""rsi.core.domain + rsi.core.evaluate: Domain.run error handling, Trial
serialization, and Evaluator aggregation exactly as RRSI's ``aggregate``:
S = mean over tasks of mean trial score with missing trials = 0 over the full
denominator k; C = mean tokens over trials that reported tokens."""
from __future__ import annotations

import json
import threading

import numpy as np
import pytest

from rsi.core import (Artifact, Domain, EvalResult, Evaluator, Execution, FunctionDomain, MockLLM, SealedSplitError,
                      Task, TaskSuite, Trial)


def _suite(n=4, fams=("x", "y")):
    ts = [Task(f"t{i}", i, i, fams[i % len(fams)], {"entities": [f"Name{i}"]}) for i in range(n)]
    return TaskSuite(ts, {"evolve": [t.id for t in ts[: n // 2]], "holdout": [t.id for t in ts[n // 2:]]})


ART = Artifact({"f": "1"})


# ------------------------------------------------------------------ Domain.run
class _Dom(Domain):
    def __init__(self, suite, execute, grade=lambda t, ex: (1.0, "ok")):
        super().__init__(suite)
        self._e, self._g = execute, grade

    def execute(self, artifact, task, *, seed, llm):
        return self._e(artifact, task, seed, llm)

    def grade(self, task, execution):
        return self._g(task, execution)


def test_run_success_carries_execution_fields():
    d = _Dom(_suite(), lambda a, t, s, l: Execution(output="o", trace="tr", tokens=7, cost_usd=0.1, steps=2,
                                                     meta={"m": 1}))
    tr = d.run(ART, d.tasks.get("t1"), seed=5)
    assert (tr.task_id, tr.seed, tr.score, tr.feedback, tr.output) == ("t1", 5, 1.0, "ok", "o")
    assert (tr.trace, tr.tokens, tr.cost_usd, tr.steps, tr.meta, tr.family) == ("tr", 7, 0.1, 2, {"m": 1}, "y")
    assert tr.ok and tr.latency_s >= 0


def test_run_execute_exception_is_graded_failure():
    def boom(a, t, s, l):
        raise KeyError("nope")

    graded = []
    d = _Dom(_suite(), boom, lambda t, ex: graded.append(1) or (1.0, ""))
    tr = d.run(ART, d.tasks.get("t0"))
    assert tr.score == 0.0 and not tr.ok and tr.error.startswith("KeyError")
    assert "execution error" in tr.feedback and "Traceback" in tr.trace
    assert graded == []                                  # the grader is not called on failed executions


def test_run_execution_with_error_field_is_not_graded():
    d = _Dom(_suite(), lambda a, t, s, l: Execution(output="partial", error="infra: down"))
    tr = d.run(ART, d.tasks.get("t0"))
    assert tr.score == 0.0 and tr.error == "infra: down" and tr.output == "partial"


def test_run_grader_exception_and_bad_scores():
    def bad_grade(t, ex):
        raise ValueError("grader broke")

    tr = _Dom(_suite(), lambda a, t, s, l: Execution(output=1), bad_grade).run(ART, _suite().get("t0"))
    assert tr.score == 0.0 and tr.feedback.startswith("grader error: ValueError") and tr.error is None
    # a grader returning a non-number is a grader error, not a loop crash (bug fix)
    tr = _Dom(_suite(), lambda a, t, s, l: Execution(output=1), lambda t, ex: (None, "")).run(ART, _suite().get("t0"))
    assert tr.score == 0.0 and tr.feedback.startswith("grader error")


def test_run_system_exit_from_artifact_code_is_contained():
    """Bug fix: SystemExit raised by artifact code escaped Domain.run and killed the loop."""
    def exits(a, t, s, l):
        raise SystemExit(2)

    tr = _Dom(_suite(), exits).run(ART, _suite().get("t0"))
    assert tr.score == 0.0 and tr.error == "SystemExit: 2"


def test_run_accepts_bare_output_from_execute():
    d = _Dom(_suite(), lambda a, t, s, l: "raw", lambda t, ex: (float(ex.output == "raw"), ""))
    assert d.run(ART, d.tasks.get("t0")).score == 1.0


def test_function_domain_wrappers_and_describe():
    fd = FunctionDomain(_suite(), lambda a, t, s, l: t.input * 2, lambda t, out: float(out == t.target * 2),
                        name="fd", description="doubles")
    tr = fd.run(ART, fd.tasks.get("t1"))
    assert tr.score == 1.0 and tr.feedback == "" and tr.output == 2
    assert fd.describe() == "doubles" and fd.name == "fd"
    fd2 = FunctionDomain(_suite(), lambda a, t, s, l: Execution(output=3, tokens=9), lambda t, o: (0.5, "half"))
    tr2 = fd2.run(ART, fd2.tasks.get("t0"))
    assert (tr2.score, tr2.feedback, tr2.tokens) == (0.5, "half", 9)
    assert "improve the artifact" in FunctionDomain(_suite(), None, None).describe()


def test_base_domain_abstract_methods():
    d = Domain(_suite())
    tr = d.run(ART, d.tasks.get("t0"))
    assert tr.error.startswith("NotImplementedError")


def test_leakage_terms_and_smoke():
    s = _suite(6)
    s.tasks["t1"] = Task("t1", 1, "answer-long", "y", {"entities": ["Alice"]})
    fd = FunctionDomain(s, lambda a, t, st, l: t.input, lambda t, o: 1.0)
    terms = fd.leakage_terms("evolve")
    assert {"t0", "t1", "t2", "answer-long", "Alice", "Name0"} <= set(terms)
    assert "0" not in terms and "2" not in terms           # targets shorter than 3 chars are not terms
    assert terms == sorted(set(terms))
    assert "t3" in fd.leakage_terms("holdout")             # reads sealed splits explicitly
    assert fd.smoke(ART) is None

    def crash(a, t, st, l):
        raise RuntimeError("dead")

    assert "dead" in FunctionDomain(s, crash, lambda t, o: 1.0).smoke(ART)


# --------------------------------------------------------------- Trial.to_json
def test_trial_to_json_roundtrip_and_truncation():
    tr = Trial("t", 1, 0.5, "fb", output={"a": [1, 2]}, trace="x" * 100, tokens=3, meta={"k": (1, 2)})
    d = tr.to_json(max_trace=10)
    assert d["trace"] == "x" * 10 + "...[truncated]"
    assert Trial(**d).output == {"a": [1, 2]}
    json.dumps(d)
    d["meta"]["k"] = "mutated"                              # to_json returns a copy
    assert tr.meta == {"k": (1, 2)}


def test_trial_to_json_non_serializable_output_and_meta():
    """Bug fix: a non-JSON value in Trial.meta crashed the Evaluator's disk cache."""
    obj = object()
    tr = Trial("t", 0, 1.0, output=obj, meta={"o": obj, "ok": 1, "nested": [obj]})
    d = tr.to_json()
    json.dumps(d)
    assert d["output"] == repr(obj) and d["meta"]["ok"] == 1 and d["meta"]["o"] == repr(obj)
    assert d["meta"]["nested"] == [repr(obj)]


# ------------------------------------------------------------------- Evaluator
class _Scripted(Domain):
    """Scores/tokens/errors scripted per (task, seed)."""

    def __init__(self, suite, table):
        super().__init__(suite)
        self.table = table          # (tid, seed) -> (score, tokens, error)
        self.calls = 0
        self._lock = threading.Lock()

    def execute(self, artifact, task, *, seed, llm):
        with self._lock:
            self.calls += 1
        score, tokens, err = self.table.get((task.id, seed), (1.0, 10, None))
        return Execution(output=score, tokens=tokens, cost_usd=0.01, steps=seed + 1, error=err)

    def grade(self, task, ex):
        return ex.output, "fb"


def test_aggregation_missing_trials_count_zero_full_denominator():
    s = _suite(4)
    table = {("t0", 0): (1.0, 100, None), ("t0", 1): (1.0, 0, "infra: backend down"), ("t0", 2): (0.0, 50, None),
             ("t1", 0): (1.0, 0, None), ("t1", 1): (1.0, 30, None), ("t1", 2): (0.5, 20, None)}
    ev = Evaluator(_Scripted(s, table), workers=1).evaluate(ART, "evolve", k=3)
    assert ev.k == 3 and ev.n_missing == 1 and ev.n_trials == 6
    ts = ev.task_scores()
    assert ts["t0"] == pytest.approx(1 / 3)                # the missing trial is 0 over denominator 3
    assert ts["t1"] == pytest.approx(2.5 / 3)
    assert ev.score == ev.S == pytest.approx((1 / 3 + 2.5 / 3) / 2)
    # C: mean over trials that reported tokens (100, 50, 30, 20) - zero-token trials excluded
    assert ev.cost == ev.C == pytest.approx(50.0)
    assert ev.error_rate == pytest.approx(1 / 6)
    assert ev.trials["t0"][1].error.startswith("infra:") and ev.trials["t0"][1].score == 0.0
    np.testing.assert_allclose(ev.trial_matrix(), [[1, 0, 0], [1, 1, 0.5]])
    assert ev.family_scores() == {"x": pytest.approx(1 / 3), "y": pytest.approx(2.5 / 3)}
    assert ev.dollars == pytest.approx(0.05)               # the missing trial carries no cost
    summ = ev.summary()
    assert summ["missing"] == 1 and summ["k"] == 3 and summ["n_tasks"] == 2


def test_missing_slots_never_shrink_the_denominator():
    ev = EvalResult("a", "evolve", {"t0": [Trial("t0", 0, 1.0)], "t1": []}, k=4)
    assert ev.task_scores() == {"t0": 0.25, "t1": 0.0}
    assert ev.score == pytest.approx(0.125)
    np.testing.assert_allclose(ev.trial_matrix(), [[1, 0, 0, 0], [0, 0, 0, 0]])
    empty = EvalResult("a", "evolve", {}, k=1)
    assert empty.score == 0.0 and empty.cost == 0.0 and empty.steps == 0.0 and empty.error_rate == 0.0


def test_worst_best_steps_and_to_json():
    s = _suite(4)
    s.splits["evolve"] = ["t0", "t1", "t2", "t3"]
    table = {("t0", 0): (0.0, 1, None), ("t1", 0): (0.5, 1, None), ("t2", 0): (1.0, 1, None), ("t3", 0): (0.2, 1, None)}
    ev = Evaluator(_Scripted(s, table), workers=2).evaluate(ART, "evolve")
    assert [t.task_id for t in ev.worst(2)] == ["t0", "t3"]
    assert [t.task_id for t in ev.best(1)] == ["t2"]
    assert ev.steps == 1.0
    d = ev.to_json()
    json.dumps(d)
    assert d["summary"]["S"] == round(ev.score, 4) and set(d["trials"]) == {"t0", "t1", "t2", "t3"}


def test_parallel_equals_serial_and_trial_order_follows_seeds():
    s = _suite(8)
    s.splits["evolve"] = [f"t{i}" for i in range(8)]
    table = {(f"t{i}", j): ((i * 7 + j) % 3 / 2, i + j + 1, None) for i in range(8) for j in range(4)}
    a = Evaluator(_Scripted(s, table), workers=1).evaluate(ART, "evolve", k=4)
    b = Evaluator(_Scripted(s, table), workers=4).evaluate(ART, "evolve", k=4)
    assert a.task_scores() == b.task_scores() and a.cost == b.cost
    assert [t.seed for t in b.trials["t3"]] == [0, 1, 2, 3]
    assert a.task_ids == [f"t{i}" for i in range(8)]


def test_memory_cache_and_n_rollouts():
    dom = _Scripted(_suite(4), {})
    ev = Evaluator(dom, workers=1)
    ev.evaluate(ART, "evolve", k=2)
    assert ev.n_rollouts == 4 and dom.calls == 4
    ev.evaluate(ART, "evolve", k=3)                    # seeds 0,1 cached; seed 2 fresh
    assert ev.n_rollouts == 6 and dom.calls == 6
    ev.evaluate(Artifact({"f": "2"}), "evolve", k=1)    # another artifact id: fresh
    assert ev.n_rollouts == 8


def test_infra_failures_are_not_cached_and_retry(tmp_path):
    s = _suite(2)
    table = {("t0", 0): (0.0, 0, "infra: backend down")}
    dom = _Scripted(s, table)
    ev = Evaluator(dom, workers=1, cache_dir=tmp_path)
    r1 = ev.evaluate(ART, "evolve")
    assert r1.n_missing == 1 and r1.score == 0.0
    del table[("t0", 0)]                                # backend recovers
    r2 = ev.evaluate(ART, "evolve")
    assert r2.n_missing == 0 and r2.score == 1.0 and dom.calls == 2


def test_disk_cache_persists_across_evaluators(tmp_path):
    dom = _Scripted(_suite(4), {("t1", 0): (0.25, 5, None)})
    ev1 = Evaluator(dom, workers=2, cache_dir=tmp_path)
    r1 = ev1.evaluate(ART, "evolve")
    dom2 = _Scripted(_suite(4), {})                     # would score 1.0 if actually run
    ev2 = Evaluator(dom2, workers=2, cache_dir=tmp_path)
    r2 = ev2.evaluate(ART, "evolve")
    assert dom2.calls == 0 and ev2.n_rollouts == 0
    assert r2.task_scores() == r1.task_scores() and r2.trials["t1"][0].feedback == "fb"
    assert not list(tmp_path.rglob("*.tmp"))


def test_disk_cache_corrupt_or_foreign_entries_rerun(tmp_path):
    dom = _Scripted(_suite(4), {})
    ev = Evaluator(dom, workers=1, cache_dir=tmp_path)
    ev.evaluate(ART, "evolve")
    files = sorted(tmp_path.rglob("*.json"))
    assert len(files) == 2
    files[0].write_text('{"task_id": "t0", "se')                     # torn write
    d = json.loads(files[1].read_text())
    d["field_from_a_newer_version"] = 1
    files[1].write_text(json.dumps(d))
    ev2 = Evaluator(dom, workers=1, cache_dir=tmp_path)
    r = ev2.evaluate(ART, "evolve")
    assert r.score == 1.0 and ev2.n_rollouts == 1       # corrupt one re-run, foreign-field one reused


def test_seed_offset_and_explicit_seeds():
    seen = []
    fd = FunctionDomain(_suite(2), lambda a, t, s, l: seen.append(s) or 1, lambda t, o: 1.0)
    ev = Evaluator(fd, workers=1, seed_offset=100)
    r = ev.evaluate(ART, "evolve", k=2)
    assert sorted(seen) == [100, 101] and [t.seed for t in r.trials["t0"]] == [100, 101]
    r2 = Evaluator(fd, workers=1).evaluate(ART, "evolve", seeds=[7, 3])
    assert r2.k == 2 and [t.seed for t in r2.trials["t0"]] == [7, 3]


def test_duplicate_seeds_do_not_halve_scores():
    """Bug fix: seeds=[0, 0] left an empty slot that counted as a 0-score trial."""
    fd = FunctionDomain(_suite(2), lambda a, t, s, l: 1, lambda t, o: 1.0)
    r = Evaluator(fd, workers=1).evaluate(ART, "evolve", seeds=[0, 0, 1])
    assert r.k == 2 and r.score == 1.0


def test_sealed_split_refusal_and_custom_task_lists():
    fd = FunctionDomain(_suite(4), lambda a, t, s, l: 1, lambda t, o: 1.0)
    with pytest.raises(SealedSplitError):
        Evaluator(fd, workers=1).evaluate(ART, "holdout")
    assert Evaluator(fd, workers=1, allow_sealed=True).evaluate(ART, "holdout").score == 1.0
    tasks = [fd.tasks.get("t0"), fd.tasks.get("t0")]
    r = Evaluator(fd, workers=1).evaluate(ART, tasks, label="mine")
    assert r.split == "mine" and r.task_ids == ["t0"]
    assert Evaluator(fd, workers=1).evaluate(ART, tasks).split == "custom"


def test_on_trial_callbacks_only_for_fresh_rollouts():
    fd = FunctionDomain(_suite(4), lambda a, t, s, l: 1, lambda t, o: 1.0)
    ev = Evaluator(fd, workers=2)
    got = []
    ev.on_trial.append(lambda art, tr: got.append((art.id, tr.task_id)))
    ev.evaluate(ART, "evolve")
    ev.evaluate(ART, "evolve")
    assert sorted(got) == [(ART.id, "t0"), (ART.id, "t1")]


def test_evaluate_many_and_llm_passthrough():
    llm = MockLLM(lambda p, s, seed, i: "7")
    fd = FunctionDomain(_suite(2), lambda a, t, s, l: l.complete("q", seed=s, role="task").text,
                        lambda t, o: float(o == "7"))
    ev = Evaluator(fd, llm, workers=1)
    res = ev.evaluate_many([ART, Artifact({"f": "2"})], "evolve", k=1)
    assert [r.score for r in res] == [1.0, 1.0]
    assert llm.meter.snapshot()["task"]["calls"] == 2
