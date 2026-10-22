"""Regression tests for the second core-qa review round: each failed before its fix."""
from __future__ import annotations

import json
import math
import os
import random
import statistics
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest

from rsi.core import (AgentEditor, Artifact, CachedLLM, Domain, DualGate, Evaluator, Execution, FunctionDomain,
                      GateContext, ImprovementResult, Ledger, LLM, LLMResponse, MockLLM, Scored, StrictImprovement,
                      Task, TaskSuite, Usage, artifact_usage, call_function, noise_from_trials, run_cmd, run_python,
                      select)
from rsi.core.gates import Gate, Verdict
from rsi.domains.agentqa import AgentQADomain, make_suite
from tests.core.test_core_editors_critic import FakeAgentCLI


def _suite(n=3):
    ts = [Task(f"t{i}", i, i) for i in range(n)]
    return TaskSuite(ts, {"evolve": [t.id for t in ts]})


@pytest.mark.parametrize("out", ["nan", "inf", "-inf"])
def test_non_finite_grader_score_is_a_grader_error(out):
    fd = FunctionDomain(_suite(), lambda a, t, s, l: a["x"], lambda t, o: -abs(float(o) - t.target))
    tr = fd.run(Artifact({"x": out}), fd.tasks.get("t1"))
    assert tr.score == 0.0 and tr.feedback.startswith("grader error: non-finite score")
    assert math.isfinite(Evaluator(fd, workers=1).evaluate(Artifact({"x": out}), "evolve").score)


class _Loss(Domain):
    """Scores are -loss in [-10, 0]: a crash must score -10, not the best score 0."""
    name = "neg-loss"
    score_range = (-10.0, 0.0)

    def execute(self, artifact, task, *, seed, llm):
        if artifact["mode"] == "crash":
            raise RuntimeError("boom")
        if artifact["mode"] == "infra":
            return Execution(error="infra: backend down")
        return Execution(output=float(artifact["mode"]))

    def grade(self, task, execution):
        return -abs(execution.output - task.target), ""


def test_failure_score_follows_score_range():
    d = _Loss(_suite())
    assert d.failure_score == -10.0
    assert d.run(Artifact({"mode": "crash"}), d.tasks.get("t1")).score == -10.0
    good = Artifact({"mode": "1"})
    assert d.run(good, d.tasks.get("t1")).score == 0.0
    ev = Evaluator(d, workers=1)
    assert ev.evaluate(Artifact({"mode": "crash"})).score == -10.0 < ev.evaluate(good).score
    miss = ev.evaluate(Artifact({"mode": "infra"}))
    assert miss.n_missing == 3 and miss.score == -10.0            # missing trials get the failure score too
    assert Domain(_suite()).failure_score == 0.0                  # default range: unchanged behaviour


def test_dual_gate_rejects_nan_capability():
    g = DualGate({"score": 0.01}, {"tokens": 0.0})
    inc = Scored(0.9, metrics={"score": 0.9, "tokens": 10.0})
    nan = Scored(float("nan"), metrics={"score": float("nan"), "tokens": 1.0})
    assert not g.check(nan, inc, GateContext()).accept
    assert g.check(Scored(0.9, metrics={"score": 0.9, "tokens": 5.0}), inc, GateContext()).accept


class _Always(Gate):
    name = "always"

    def check(self, cand, inc, ctx):
        return Verdict(True, "ok")


def test_select_never_returns_a_nan_candidate():
    cands = [("nan", Scored(float("nan"))), ("good", Scored(0.5)), ("worse", Scored(0.2))]
    winner, verdicts = select(cands, Scored(0.0), _Always(), GateContext())
    assert winner == "good"
    assert not verdicts[0][1].accept and "NaN" in verdicts[0][1].reason
    assert select(cands[:1], Scored(0.0), _Always(), GateContext())[0] is None
    tie = [("a", Scored(0.5, cost=2.0)), ("b", Scored(0.5, cost=1.0)), ("c", Scored(0.5, cost=1.0))]
    assert select(tie, Scored(0.0), _Always(), GateContext(), key=lambda s: (s.score, -s.cost))[0] == "b"   # tuple keys
    assert select(tie, Scored(0.0), StrictImprovement(), GateContext())[0] == "a"


def _answering_domain(suite, name="d"):
    return FunctionDomain(suite, lambda a, t, s, llm: llm.complete(str(t.input)).text,
                          lambda t, o: float(o == "B"), name=name)


def test_disk_cache_is_keyed_on_task_model(tmp_path):
    dom, art = _answering_domain(_suite()), Artifact({"x": "1"})
    a, b = MockLLM(lambda *_: "A", name="model-a"), MockLLM(lambda *_: "B", name="model-b")
    assert Evaluator(dom, a, cache_dir=tmp_path).evaluate(art).score == 0.0
    ev_b = Evaluator(dom, b, cache_dir=tmp_path)
    assert ev_b.evaluate(art).score == 1.0 and ev_b.n_rollouts == 3
    ev_b2 = Evaluator(dom, b, cache_dir=tmp_path)                 # same model again: replayed from disk
    assert ev_b2.evaluate(art).score == 1.0 and ev_b2.n_rollouts == 0
    ev_c = Evaluator(dom, CachedLLM(b, tmp_path / "llm"), cache_dir=tmp_path)   # a wrapper replays the same model
    assert ev_c.evaluate(art).score == 1.0 and ev_c.n_rollouts == 0


def test_disk_cache_is_keyed_on_task_content(tmp_path):
    llm, art = MockLLM(lambda p, *_: "B" if p == "7" else "A", name="m"), Artifact({"x": "1"})
    s1 = TaskSuite([Task("t0", 7, None)], {"evolve": ["t0"]})
    s2 = TaskSuite([Task("t0", 8, None)], {"evolve": ["t0"]})     # same task id, as make_suite across seeds
    assert Evaluator(_answering_domain(s1), llm, cache_dir=tmp_path).evaluate(art).score == 1.0
    assert Evaluator(_answering_domain(s2), llm, cache_dir=tmp_path).evaluate(art).score == 0.0
    ev = Evaluator(_answering_domain(s1, name="other"), llm, cache_dir=tmp_path)
    ev.evaluate(art)
    assert ev.n_rollouts == 1                                     # another domain name: not replayed


def test_disk_cache_reads_legacy_entries_without_identity(tmp_path):
    dom, art, llm = _answering_domain(_suite(1)), Artifact({"x": "1"}), MockLLM(lambda *_: "A", name="m")
    Evaluator(dom, llm, cache_dir=tmp_path).evaluate(art)
    (entry,) = list(tmp_path.rglob("*.json"))
    d = json.loads(entry.read_text())
    assert d["_cache_identity"]["llm"] == "m" and d["_cache_identity"]["domain"] == "d"
    d.pop("_cache_identity")
    d["score"] = 0.25                                             # a pre-identity entry is still served
    entry.write_text(json.dumps(d))
    ev = Evaluator(dom, llm, cache_dir=tmp_path)
    assert ev.evaluate(art).score == 0.25 and ev.n_rollouts == 0


class _Paid(LLM):
    name = "paid"

    def _complete(self, prompt, *, system, max_tokens, seed):
        return LLMResponse("ANSWER: 1", Usage(1, 100, 10, 0.01, 0.0), "paid")


def test_cached_hit_carries_original_usage(tmp_path):
    c = CachedLLM(_Paid(), tmp_path)
    fresh, hit = c.complete("q", role="task"), c.complete("q", role="task")
    assert artifact_usage(fresh) == fresh.usage and fresh.raw is None
    assert hit.usage.cost_usd == 0.0 and hit.usage.calls == 0                           # spend: nothing
    assert hit.raw["cached"] and artifact_usage(hit) == Usage(1, 100, 10, 0.01, 0.0)   # represents: the original
    assert c.meter.total().cost_usd == pytest.approx(0.01)
    bad = LLMResponse("x", Usage(1, 1, 1, 0.5), "m", raw={"cached": True, "usage": "bad"})
    assert artifact_usage(bad).cost_usd == 0.5


def test_agentqa_trial_cost_is_the_same_on_a_replay(tmp_path):
    dom = AgentQADomain(make_suite(n_evolve=2, n_holdout=1, n_ood_per_family=1))
    seed = AgentQADomain.seed_artifact()
    r1, r2 = (Evaluator(dom, CachedLLM(_Paid(), tmp_path), workers=1).evaluate(seed) for _ in range(2))
    assert r1.dollars == pytest.approx(0.02) and r2.dollars == pytest.approx(r1.dollars)
    assert r1.cost == r2.cost == 110.0


@pytest.mark.parametrize("name", ["../escape.txt", "/tmp/abs.txt", "a/../../b.py"])
def test_call_function_refuses_escaping_extra_files(name, tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    with pytest.raises(ValueError, match="unsafe extra_files"):
        call_function("def f():\n    return 1\n", "f", {}, extra_files={name: "x"})
    assert list(tmp_path.iterdir()) == []                          # nothing written, no scratch dir leaked
    assert call_function("import helper\ndef f():\n    return helper.V\n", "f", {},
                         extra_files={"helper.py": "V = 3\n"})[0] == 3


def test_run_cmd_kills_its_child_when_interrupted(monkeypatch):
    seen, orig = {}, subprocess.Popen.communicate

    def interrupted(self, input=None, timeout=None):
        if "pid" not in seen:
            seen["pid"] = self.pid
            raise KeyboardInterrupt
        return orig(self, input=input, timeout=timeout)

    monkeypatch.setattr(subprocess.Popen, "communicate", interrupted)
    t0 = time.time()
    with pytest.raises(KeyboardInterrupt):
        run_cmd([sys.executable, "-c", "import time; time.sleep(30)"], timeout_s=60)
    assert time.time() - t0 < 10
    with pytest.raises(ProcessLookupError):                        # killed and reaped
        os.kill(seen["pid"], 0)


def test_run_cmd_is_thread_safe_and_keeps_limits():
    with ThreadPoolExecutor(12) as pool:
        outs = list(pool.map(lambda i: run_python(f"print({i} * 2)", timeout_s=30).stdout.strip(), range(12)))
    assert outs == [str(2 * i) for i in range(12)]
    rr = run_cmd([sys.executable, "-c", "import resource; print(resource.getrlimit(resource.RLIMIT_AS)[0])"],
                 mem_mb=512)
    assert int(rr.stdout) == 512 * 1024 * 1024
    rr = run_cmd([sys.executable, "-c", "import os; print(os.getsid(0) == os.getpid())"])
    assert rr.stdout.strip() == "True"                             # own session: the timeout killpg is scoped


def test_agent_editor_skips_escaping_context_names():
    base = Artifact({"harness.py": "orig\n", "notes.md": "n\n"})
    p = AgentEditor(FakeAgentCLI(edits={"notes.md": "new\n"})).edit(
        base, "go", context={"../harness.py": "OVERWRITTEN\n", "history.md": "h\n"})
    assert p.ok and p.artifact["harness.py"] == "orig\n" and p.artifact["notes.md"] == "new\n"
    assert p.meta["skipped_context"] == ["../harness.py"]


def test_improvement_result_save_accepts_numpy_values(tmp_path):
    r = ImprovementResult("m", Artifact({"a": "1"}), Artifact({"a": "2"}), Ledger(),
                          trajectory=[{"S": np.float32(0.5), "n": np.int64(3), "per_task": np.array([0.1, 0.2])}])
    r.save(tmp_path)
    assert json.loads((tmp_path / "trajectory.json").read_text()) == [{"S": 0.5, "n": 3.0, "per_task": [0.1, 0.2]}]


def _rrsi_bootstrap_se(matrix, reps=2000, seed=7):
    """Line-by-line port of rrsi/calibrate.py:bootstrap_se (unit weights, random.Random, pstdev)."""
    rng = random.Random(seed)
    vals = []
    for _ in range(reps):
        num = den = 0.0
        for rewards in matrix:
            n = len(rewards)
            for _j in range(n):
                num += rewards[rng.randrange(n)]
                den += 1.0
        vals.append(num / den)
    return statistics.pstdev(vals)


@pytest.mark.parametrize("k", [2, 4])
def test_noise_from_trials_matches_rrsi_bootstrap(k):
    """delta = z * sqrt(2) * se with RRSI's within-task bootstrap se, up to Monte-Carlo error."""
    rng = np.random.default_rng(k)
    p = rng.uniform(0.05, 0.95, size=40)
    m = (rng.random((40, k)) < p[:, None]).astype(float)
    ours = noise_from_trials(m, z=2.0)
    assert ours.delta == pytest.approx(2.0 * math.sqrt(2) * _rrsi_bootstrap_se(m.tolist()), rel=0.06)
    plug_in = math.sqrt(sum(np.var(r) / k for r in m)) / len(m)    # both use the ddof=0 within-task variance
    assert ours.sd_null / math.sqrt(2) == pytest.approx(plug_in, rel=0.06)
