"""Discovery domains: locked evaluators, typed failures, mock agents, generic rsi.core Domain support."""
import json
import random

import numpy as np
import pytest

from rsi.core import Artifact, transfer_report
from rsi.domains.discovery import (CirclePackingDomain, LassoPathDomain, SumDiffDomain, SyntheticConfig,
                                   SyntheticDomain, gamma, solver_code)
from rsi.domains.discovery.lasso import SEED_KNOBS
from rsi.dream import AttemptContext, Config, DomainTask, run


def ctx_for(ws, direction, attempt=0, branch=0, rnd=1):
    return AttemptContext("p", "root", ws, None, branch, attempt, rnd, direction={"direction": direction})


def test_sumdiff_evaluator():
    assert gamma([0, 2, 3, 4, 7, 11, 12, 14])[0] == pytest.approx(1.0344212156, abs=1e-9)
    dom = SumDiffDomain(sandboxed=False)
    ev = dom.evaluate_program(dom.seed_artifact())
    assert ev.fail_class == "ok" and 0.9 < ev.score < 1.0
    bad = Artifact({"construct.py": "def construct():\n    return [1, 1, 2]\n"})
    assert dom.evaluate_program(bad).fail_class == "constraint"
    bad2 = Artifact({"construct.py": "def construct():\n    return 'abc'\n"})
    assert dom.evaluate_program(bad2).fail_class == "correctness"
    crash = Artifact({"construct.py": "def construct():\n    raise ValueError('x')\n"})
    assert dom.evaluate_program(crash).fail_class == "compile_other"
    agent = dom.mock_agent()
    child = agent.attempt(ctx_for(dom.seed_artifact(), "window"), seed=1)
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
    assert dom.check(overlap).fail_class == "constraint"
    outside = [[0.05, 0.5, 0.1]] + [[0.1 + 0.03 * i, 0.9, 0.001] for i in range(25)]
    assert dom.check(outside).fail_class == "constraint"
    assert dom.check([[0.5, 0.5, 0.1]]).fail_class == "correctness"
    from rsi.dream import workspace_of
    ws = workspace_of(dom.seed_artifact(), "", ev)
    child = dom.mock_agent().attempt(ctx_for(ws, "polish"), seed=3)
    assert "INIT = [[" in child.artifact["pack.py"]          # resumes the parent's measured packing
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
    agent = dom.mock_agent()
    root = dom.seed_artifact()
    a1 = agent.attempt(ctx_for(root, "d1", branch=2), seed=0)
    a2 = agent.attempt(ctx_for(root, "d1", branch=2), seed=999)      # CRN: the seed argument is irrelevant
    assert a1.artifact == a2.artifact
    st = json.loads(a1.artifact["state.json"])
    truth = dom.world.ground_truth(st["branch"], 4)
    assert truth >= 0.0
    lin = {"d1": 2}
    assert dom.world.r_eff("d1", lin) == pytest.approx(dom.world.richness["d1"] * dom.cfg.depletion ** 2)
    res = run(dom, config=Config(rounds=1, W=3, branch_count=3, refine_count=2, dream=False, sandbox="inprocess"))
    tree = res.meta["worlds"][0]
    gt = dom.truth_for_tree(tree)
    assert set(gt) == set(tree.branches())


def test_context_coupling_changes_outcomes():
    base = SyntheticDomain(SyntheticConfig(seed=1)).world
    coup = SyntheticDomain(SyntheticConfig(seed=1, context_coupling=0.5)).world
    br = base.branch(1, 0, "d0", 0.2, {})
    assert base.outcome(1, 0, 1, br, 0.2, n_siblings=0) == base.outcome(1, 0, 1, br, 0.2, n_siblings=20)
    q0, q20 = coup.outcome(1, 0, 1, br, 0.2, 0), coup.outcome(1, 0, 1, br, 0.2, 20)
    if q0[0] is not None and q20[0] is not None and br["G"] > 0:
        assert q20[0] > q0[0]


def test_dream_on_generic_rsi_core_domain_agentqa():
    from rsi.domains.agentqa import AgentQADomain, SimModel, make_suite
    from rsi.domains.discovery.agentqa_agent import MECHANISMS, agentqa_mock_agent

    suite = make_suite(n_evolve=6, n_holdout=6, n_ood_per_family=2, seed=1)
    dom = AgentQADomain(suite)
    sim = SimModel(suite)
    task = DomainTask(dom, dom.seed_artifact(), sim, directions=MECHANISMS, workers=2)
    res = run(task, llm_task=sim, agent=agentqa_mock_agent(),
              config=Config(rounds=2, W=2, branch_count=3, refine_count=1, M=2, sandbox="inprocess"))
    assert res.meta["best_score"] > res.meta["seed_score"]
    assert "harness.py" in res.best
    usage = res.usage
    assert usage["task"]["calls"] > 0            # the frozen task model is metered per role


def test_domain_task_failure_classes():
    from rsi.core import FunctionDomain, Task, TaskSuite

    ts = TaskSuite([Task("t0", 1, 1), Task("t1", 2, 2)], {"evolve": ["t0", "t1"]})

    def ex(art, task, seed, llm):
        mode = art["mode"]
        if mode == "crash":
            raise ValueError("boom")
        if mode == "infra":
            from rsi.core import Execution
            return Execution(error="infra: backend down")
        return task.input

    dom = FunctionDomain(ts, ex, lambda t, o: float(o == t.target))
    task = DomainTask(dom, Artifact({"mode": "ok"}))
    assert task.evaluate(Artifact({"mode": "ok"})).fail_class == "ok"
    assert task.evaluate(Artifact({"mode": "crash"})).fail_class == "compile_other"
    assert task.evaluate(Artifact({"mode": "infra"})).fail_class == "env_error"
