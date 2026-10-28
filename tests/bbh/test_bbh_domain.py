"""BBHDomain on the AgentQA harness interface, and every method running on it offline with the AgentQA mocks."""
from __future__ import annotations

import json
import warnings

import pytest

import rsi
from rsi.core import Artifact, Evaluator, Execution, MockLLM
from rsi.domains.agentqa import AgentQADomain
from rsi.domains.bbh import FAMILY_FILE, BBHDomain, BBHSimModel, cot3_artifact, make_suite
from rsi.harness2 import MockImprover
from rsi.harness2.guard import markers_for


@pytest.fixture
def small(kw):
    suite = make_suite(["multistep_arithmetic_two", "dyck_languages", "date_understanding"],
                       ["navigate", "object_counting"], per_family={"evolve": 2, "holdout": 2, "ood": 2}, **kw)
    return BBHDomain(suite), BBHSimModel(suite)


def test_is_an_agentqa_domain_with_bbh_hooks(small):
    dom, _ = small
    assert isinstance(dom, AgentQADomain) and dom.name == "bbh"
    assert dom.validate_artifact(dom.seed_artifact()) is None
    assert "ANSWER:" in dom.seed_artifact()["prompts/task.md"] and "step by step" not in str(dict(dom.seed_artifact()))
    assert "grade" not in dom.harness_brief().lower()
    assert dom.grader_verdict_markers() == AgentQADomain.grader_verdict_markers(dom)
    t = dom.tasks.split("evolve")[0]
    assert dom.domain_of(t) == t.family
    assert "(A)" not in dom.leakage_terms() and "True" not in dom.leakage_terms()
    assert all(f in dom.describe() for f in dom.tasks.families("evolve"))


def test_grade_uses_bbh_conventions_and_agentqa_feedback(small):
    dom, _ = small
    t = next(x for x in dom.tasks.split("evolve") if x.family == "date_understanding")
    ok, fb = dom.grade(t, Execution(output=f"reasoning...\nANSWER: {t.target.strip('()')}"))
    assert ok == 1.0 and fb.startswith("Correct (answer ")
    wrong = "(A)" if t.target != "(A)" else "(B)"
    ok, fb = dom.grade(t, Execution(output=f"ANSWER: {wrong}"))
    assert ok == 0.0 and fb == f"Incorrect. Extracted answer {wrong!r}; expected {t.target!r}."
    assert any(m in fb for m in markers_for(dom))          # Harness² screens this feedback like AgentQA's


def test_seed_harness_makes_one_call_with_the_question(small):
    dom, _ = small
    t = dom.tasks.split("evolve")[0]
    seen = []
    llm = MockLLM(lambda p, s, seed, i: (seen.append(p), f"ANSWER: {t.target}")[1])
    tr = dom.run(dom.seed_artifact(), t, llm=llm)
    assert tr.score == 1.0 and len(seen) == 1 and t.input in seen[0] and seen[0].rstrip().endswith("`ANSWER: <answer>`.")


def test_family_hint_only_for_the_cot_reference(small, kw):
    dom, sim = small
    probe = Artifact({"harness.py": "def solve(question, llm, tools, files):\n"
                                    f"    return 'ANSWER: ' + files.get({FAMILY_FILE!r}, 'none')\n"})
    t = dom.tasks.split("evolve")[0]
    assert dom.rollout(probe, t, llm=sim).output == "ANSWER: none"
    hinted = BBHDomain(dom.tasks, family_hint=True)
    assert hinted.rollout(probe, t, llm=sim).output == f"ANSWER: {t.family}"
    cot = cot3_artifact(dom.tasks.families(), **kw)
    prompts = []
    llm = MockLLM(lambda p, s, seed, i: (prompts.append(p), "Let's see. So the answer is (C).")[1])
    tr = hinted.run(cot, t, llm=llm)
    assert prompts[0].startswith(f"Synthetic description of {t.family}.") and prompts[0].endswith(
        "A: Let's think step by step.") and t.input in prompts[0]
    assert tr.output.endswith("ANSWER: (C)")


def test_tampering_with_bbh_grading_is_caught_and_restored(small):
    dom, sim = small
    from rsi.domains.bbh import grading
    orig = grading.is_correct
    hack = Artifact({"harness.py": "import sys\n"
                                   "def solve(question, llm, tools, files):\n"
                                   "    sys.modules['rsi.domains.bbh.grading'].is_correct = lambda *a, **k: True\n"
                                   "    return 'ANSWER: nothing'\n"})
    tr = dom.run(hack, dom.tasks.split("evolve")[0], llm=sim)
    assert tr.score == 0.0 and "tamper" in (tr.error or "") and grading.is_correct is orig


def _transfer(dom, sim, res, name):
    return rsi.transfer_report(dom, sim, {"seed": res.baseline, name: res.best}, splits=("holdout", "ood"))


def test_improve_rrsi_offline(small):
    from rsi.rrsi.mocks import AgentQAMockLLM
    dom, sim = small
    res = rsi.improve(dom, method="rrsi", llm_task=sim, llm_propose=AgentQAMockLLM(), config={"T": 3})
    assert res.best != res.baseline and dom.validate_artifact(res.best) is None
    rep = _transfer(dom, sim, res, "rrsi")
    assert set(rep["splits"]) == {"holdout", "ood"} and rep["splits"]["holdout"]["rrsi"]["missing"] == 0


def test_improve_gepa_offline(small):
    from rsi.gepa import AgentQAReflectionLM
    dom, sim = small
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        res = rsi.improve(dom, method="gepa", llm_task=sim, llm_propose=AgentQAReflectionLM(),
                          config={"max_metric_calls": 60, "acceptance": "noise_margin", "noise_margin": 0.05})
    assert res.meta["rollouts"] >= 60 and set(res.best.files) == set(res.baseline.files)
    assert _transfer(dom, sim, res, "gepa")["splits"]["ood"]["gepa"]["missing"] == 0


def test_improve_harness2_offline_never_grades_inside_the_loop(small):
    dom, sim = small
    graded = []
    orig = dom.grade

    def spy(task, ex):
        graded.append(task.id)
        return orig(task, ex)
    dom.grade = spy
    res = rsi.improve(dom, method="harness2", llm_task=sim, llm_propose=MockImprover(),
                      config={"k": 1, "split": "holdout+ood", "max_tasks": 4})
    rep = res.meta["report"]
    assert rep["n"] == 4 and len(res.trajectory) == 4 and set(rep["families"]) <= set(dom.tasks.families())
    assert len(graded) == 8                    # base + submitted per task, all in the post-hoc report


def test_metaharness_and_solpi_use_the_agentqa_offline_proposers(small):
    dom, sim = small
    mh = rsi.improve(dom, method="metaharness", llm_task=sim,
                     config={"iterations": 1, "k": 2, "finalize": False, "shadow_monitor": False})
    assert mh.meta["n_evaluated"] >= 1
    sp = rsi.improve(dom, method="solpi", llm_task=sim)
    assert sp.stop_reason


def test_autoresearch_dream_and_evomap_run_on_bbh(small, tmp_path):
    from rsi.autoresearch import Config as ARConfig, MockResearchAgent, run as ar_run
    from rsi.autoresearch.pools import harness_edit_pool
    from rsi.domains.discovery.agentqa_agent import MECHANISMS, agentqa_mock_agent
    from rsi.dream import Config as DConfig, DomainTask, run as d_run
    from rsi.evomap import AppendInjector, Config as EConfig, run as e_run
    dom, sim = small
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")         # the upstream keep rule's luck warning
        ar = ar_run(dom, dom.seed_artifact(), llm_task=sim, agent=MockResearchAgent(harness_edit_pool(), seed=0),
                    config=ARConfig(max_experiments=3, plot=False), out_dir=tmp_path / "ar", task_kwargs={"k": 1})
    assert ar.ledger.nodes()
    task = DomainTask(dom, dom.seed_artifact(), sim, directions=MECHANISMS, workers=2)
    dr = d_run(task, llm_task=sim, agent=agentqa_mock_agent(),
               config=DConfig(rounds=1, W=2, branch_count=2, refine_count=1, sandbox="inprocess"))
    assert dr.meta["worlds"]
    gene = {"id": "gene_steps", "category": "optimize", "signals_match": ["task:any"],
            "summary": "Reason step by step and verify before answering.",
            "strategy": ["Think step by step.", "Verify the result.", "End with a line 'ANSWER: <value>'."],
            "avoid": ["Guessing."], "validation": ["rsi-taskcheck --n 2"]}
    writer = MockLLM(lambda p, s, seed, i: "```json\n" + json.dumps(gene) + "\n```", name="gene-writer")
    ev = e_run(dom, dom.seed_artifact(), llm_task=sim, llm_propose=writer,
               injector=AppendInjector("prompts/system.md"),
               config=EConfig(cycles=2, mode="safe", seed=0, taskcheck_n=2, taskcheck_k=1))
    assert ev.meta["audit"]["ok"]


def test_cli_problem_bbh_offline(bbh_offline, tmp_path):
    from rsi.cli import main
    out = tmp_path / "cli"
    assert main(["improve", "--method", "rrsi", "--problem", "bbh", "--llm", "sim", "--out", str(out),
                 "--set", "T=1"]) == 0
    rep = json.loads((out / "transfer.json").read_text())
    assert {"holdout", "ood"} <= set(rep["splits"])


def test_default_domain_builds_the_default_suite(bbh_offline):
    dom = BBHDomain()
    assert len(dom.tasks.splits["evolve"]) == 36 and Evaluator(dom, BBHSimModel(dom.tasks)).evaluate(
        dom.seed_artifact(), "smoke").n_trials == 2
