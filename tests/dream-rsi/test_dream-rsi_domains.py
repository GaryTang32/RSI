"""Discovery domains: locked evaluators, typed failures, mock agents and generic ``rsi.core`` domains."""
import json

import pytest

from rsi.core import Artifact, Execution, FunctionDomain, Task, TaskSuite, transfer_report
from rsi.domains.discovery import (CirclePackingDomain, LassoPathDomain, SumDiffDomain, SyntheticConfig,
                                   SyntheticDomain, gamma, solver_code)
from rsi.domains.discovery.lasso import SEED_KNOBS
from rsi.dream import AttemptContext, Config, DomainTask, run, workspace_of


def ctx_for(ws, direction: str, branch: int = 0) -> AttemptContext:
    return AttemptContext("p", "root", ws, None, branch, 0, 1, direction={"direction": direction})


def test_sumdiff_evaluator():
    assert gamma([0, 2, 3, 4, 7, 11, 12, 14])[0] == pytest.approx(1.0344212156, abs=1e-9)
    dom = SumDiffDomain(sandboxed=False)
    ev = dom.evaluate_program(dom.seed_artifact())
    assert ev.fail_class == "ok" and 0.9 < ev.score < 1.0
    for body, fail_class in (("return [1, 1, 2]", "constraint"), ("return 'abc'", "correctness"),
                             ("raise ValueError('x')", "compile_other")):
        bad = Artifact({"construct.py": f"def construct():\n    {body}\n"})
        assert dom.evaluate_program(bad).fail_class == fail_class
    child = dom.mock_agent().attempt(ctx_for(dom.seed_artifact(), "window"), seed=1)
    ev2 = dom.evaluate_program(child.artifact)
    assert ev2.fail_class == "ok" and ev2.score >= ev.score and "window" in child.proposal


def test_sumdiff_sandboxed_matches_inprocess():
    a, b = SumDiffDomain(sandboxed=True), SumDiffDomain(sandboxed=False)
    assert a.evaluate_program(a.seed_artifact()).score == pytest.approx(b.evaluate_program(b.seed_artifact()).score)


def test_circle_packing_evaluator_and_agent():
    dom = CirclePackingDomain(sandboxed=False)
    ev = dom.evaluate_program(dom.seed_artifact())
    assert ev.fail_class == "ok" and 2.3 < ev.score < 2.64 and len(ev.diagnostics["centers"]) == 26
    overlap = [[0.5, 0.5, 0.1]] * 26
    outside = [[0.05, 0.5, 0.1]] + [[0.1 + 0.03 * i, 0.9, 0.001] for i in range(25)]
    assert dom.check(overlap).fail_class == dom.check(outside).fail_class == "constraint"
    assert dom.check([[0.5, 0.5, 0.1]]).fail_class == "correctness"
    child = dom.mock_agent().attempt(ctx_for(workspace_of(dom.seed_artifact(), "", ev), "polish"), seed=3)
    assert "INIT = [[" in child.artifact["pack.py"]  # resumes the parent's measured packing
    ev2 = dom.evaluate_program(child.artifact)
    assert ev2.fail_class == "ok" and ev2.score >= ev.score - 1e-9


def test_lasso_gate_and_holdout():
    dom = LassoPathDomain(sandboxed=False, repeats=1)
    seed = dom.evaluate_program(dom.seed_artifact())
    assert seed.fail_class == "ok" and seed.score > 0 and seed.diagnostics["max_objective_excess"] <= 1e-6
    fast = Artifact({"solver.py": solver_code({**SEED_KNOBS, "WARM": True, "ACTIVE": True, "SCREEN": True})})
    ev = dom.evaluate_program(fast)
    assert ev.fail_class == "ok" and ev.score > seed.score
    loose = Artifact({"solver.py": solver_code({**SEED_KNOBS, "ALGO": "fista", "TOL": 1e-3, "WARM": True})})
    bad = dom.evaluate_program(loose)
    assert bad.fail_class == "correctness" and bad.score == 0.0
    rep = transfer_report(dom, None, {"seed": dom.seed_artifact(), "fast": fast}, splits=("evolve", "holdout"),
                          workers=1)
    assert rep["splits"]["holdout"]["fast"]["S"] > rep["splits"]["holdout"]["seed"]["S"] > 0


def test_synthetic_world_common_random_numbers_and_truth():
    dom = SyntheticDomain(SyntheticConfig(seed=3))
    agent, root = dom.mock_agent(), dom.seed_artifact()
    a1 = agent.attempt(ctx_for(root, "d1", branch=2), seed=0)
    assert a1.artifact == agent.attempt(ctx_for(root, "d1", branch=2), seed=999).artifact  # CRN: seed is irrelevant
    assert dom.world.ground_truth(json.loads(a1.artifact["state.json"])["branch"], 4) >= 0.0
    assert dom.world.r_eff("d1", {"d1": 2}) == pytest.approx(dom.world.richness["d1"] * dom.cfg.depletion ** 2)
    res = run(dom, config=Config(rounds=1, W=3, branch_count=3, refine_count=2, dream=False, sandbox="inprocess"))
    tree = res.meta["worlds"][0]
    assert set(dom.truth_for_tree(tree)) == set(tree.branches())


def test_context_coupling_changes_outcomes():
    base = SyntheticDomain(SyntheticConfig(seed=1)).world
    coup = SyntheticDomain(SyntheticConfig(seed=1, context_coupling=0.5)).world
    br = base.branch(1, 0, "d0", 0.2, {})
    assert base.outcome(1, 0, 1, br, 0.2, n_siblings=0) == base.outcome(1, 0, 1, br, 0.2, n_siblings=20)
    q0, q20 = coup.outcome(1, 0, 1, br, 0.2, 0), coup.outcome(1, 0, 1, br, 0.2, 20)
    if q0[0] is not None and q20[0] is not None and br["G"] > 0:
        assert q20[0] > q0[0]


def test_dream_on_generic_rsi_core_domain_agentqa(agentqa_run):
    res = agentqa_run(2, rounds=2, M=2)
    assert res.meta["best_score"] > res.meta["seed_score"]
    assert "harness.py" in res.best
    assert res.usage["task"]["calls"] > 0  # the frozen task model is metered per role


def test_domain_task_failure_classes():
    def execute(art, task, seed, llm):
        if art["mode"] == "crash":
            raise ValueError("boom")
        return Execution(error="infra: backend down") if art["mode"] == "infra" else task.input

    suite = TaskSuite([Task("t0", 1, 1), Task("t1", 2, 2)], {"evolve": ["t0", "t1"]})
    task = DomainTask(FunctionDomain(suite, execute, lambda t, o: float(o == t.target)), Artifact({"mode": "ok"}))
    for mode, fail_class in (("ok", "ok"), ("crash", "compile_other"), ("infra", "env_error")):
        assert task.evaluate(Artifact({"mode": mode})).fail_class == fail_class
