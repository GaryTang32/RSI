"""BBEHDomain: the BBH/AgentQA harness interface with BBEH's grader; every method runs on it offline."""
from __future__ import annotations

import json
import warnings

import pytest

import rsi
from rsi.core import Artifact, Execution, MockLLM
from rsi.domains.agentqa import AgentQADomain
from rsi.domains.bbeh import BBEHDomain, BBEHSimModel, leak_check, make_suite
from rsi.domains.bbh import BBHDomain
from rsi.harness2 import MockImprover
from rsi.harness2.guard import markers_for


@pytest.fixture
def small(bkw):
    suite = make_suite(["multistep_arithmetic", "web_of_lies", "word_sorting"], ["object_counting", "hyperbaton"],
                       per_family={"evolve": 2, "holdout": 2, "ood": 2}, **bkw)
    return BBEHDomain(suite), BBEHSimModel(suite)


def test_is_a_bbh_and_agentqa_domain(small):
    dom, _ = small
    assert isinstance(dom, BBHDomain) and isinstance(dom, AgentQADomain) and dom.name == "bbeh"
    assert dom.seed_artifact() == BBHDomain.seed_artifact()
    assert "ANSWER: <answer>" in dom.seed_artifact()["prompts/task.md"]
    assert "first line" in dom.describe() and "(B)" not in dom.describe()
    with pytest.raises(ValueError, match="no official CoT prompts"):
        BBEHDomain(dom.tasks, family_hint=True)
    terms = dom.leakage_terms()
    assert all(len(t.split()) > 2 for t in terms if not t.startswith("evolve-"))


def test_grade_uses_the_bbeh_pipeline_with_agentqa_feedback(small):
    dom, _ = small
    t = dom.tasks.split("evolve")[0]
    ok, fb = dom.grade(t, Execution(output=f"long reasoning\nThe answer is 3.\nANSWER: **{t.target}**"))
    assert ok == 1.0 and fb.startswith("Correct (answer ")
    ok, fb = dom.grade(t, Execution(output="ANSWER: certainly wrong"))
    assert ok == 0.0 and fb == f"Incorrect. Extracted answer 'certainly wrong'; expected {t.target!r}."
    assert any(m in fb for m in markers_for(dom))


def test_tampering_with_bbeh_grading_is_caught_and_restored(small):
    dom, sim = small
    from rsi.domains.bbeh import grading
    orig = grading.fuzzy_match
    hack = Artifact({"harness.py": "import sys\n"
                                   "def solve(question, llm, tools, files):\n"
                                   "    sys.modules['rsi.domains.bbeh.grading'].fuzzy_match = lambda *a: True\n"
                                   "    return 'ANSWER: nothing'\n"})
    tr = dom.run(hack, dom.tasks.split("evolve")[0], llm=sim)
    assert tr.score == 0.0 and "tamper" in (tr.error or "") and grading.fuzzy_match is orig


def test_leak_check_uses_the_two_token_rule(small):
    dom, _ = small
    multi = next(t for t in dom.tasks.split("evolve") if len(str(t.target).split()) > 2)
    seed = dom.seed_artifact()
    bad = seed.with_files({"prompts/system.md": f"Example: {multi.target}\n"})
    assert leak_check(seed, dom.tasks)["passed"] and not leak_check(bad, dom.tasks)["passed"]


def test_seed_harness_one_call(small):
    dom, _ = small
    t = dom.tasks.split("evolve")[0]
    calls = []
    llm = MockLLM(lambda p, s, seed, i: (calls.append(p), f"The final answer is: {t.target}.")[1])
    assert dom.run(dom.seed_artifact(), t, llm=llm).score == 1.0 and len(calls) == 1


def test_improve_rrsi_gepa_harness2_offline(small):
    from rsi.gepa import AgentQAReflectionLM
    from rsi.rrsi.mocks import AgentQAMockLLM
    dom, sim = small
    r = rsi.improve(dom, method="rrsi", llm_task=sim, llm_propose=AgentQAMockLLM(), config={"T": 2})
    assert dom.validate_artifact(r.best) is None
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        g = rsi.improve(dom, method="gepa", llm_task=sim, llm_propose=AgentQAReflectionLM(),
                        config={"max_metric_calls": 40})
    assert g.meta["rollouts"] >= 40
    h = rsi.improve(dom, method="harness2", llm_task=sim, llm_propose=MockImprover(),
                    config={"k": 1, "split": "holdout+ood", "max_tasks": 3})
    assert h.meta["report"]["n"] == 3
    rep = rsi.transfer_report(dom, sim, {"seed": r.baseline, "rrsi": r.best, "gepa": g.best}, splits=("ood",))
    assert rep["splits"]["ood"]["rrsi"]["missing"] == 0


def test_metaharness_and_solpi_offline(small):
    dom, sim = small
    mh = rsi.improve(dom, method="metaharness", llm_task=sim,
                     config={"iterations": 1, "k": 1, "finalize": False, "shadow_monitor": False})
    assert mh.meta["n_evaluated"] >= 1
    assert rsi.improve(dom, method="solpi", llm_task=sim).stop_reason


def test_cli_problem_bbeh_offline(bbeh_offline, tmp_path):
    from rsi.cli import main
    out = tmp_path / "cli"
    assert main(["improve", "--method", "rrsi", "--problem", "bbeh", "--llm", "sim", "--out", str(out),
                 "--set", "T=1"]) == 0
    assert {"holdout", "ood"} <= set(json.loads((out / "transfer.json").read_text())["splits"])
