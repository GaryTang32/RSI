"""First-review regressions and a genericity test on a new ``FunctionDomain`` through the LLM paths."""
import itertools
import json
import math
import random
import re
import time

import pytest

from rsi.core import Artifact, FunctionDomain, MockLLM, SealedSplitError, Task, TaskSuite, transfer_report
from rsi.domains.discovery import SyntheticConfig, SyntheticDomain
from rsi.dream import (Config, DomainTask, GridPlanningContext, GuardViolation, PrefixGuard, QuestionProxy,
                       ReplayQuestion, mock_developer_llm, rules, run, template_code)
from rsi.dream.guard import InProcessSession

HIDDEN_T = 23  # the grader's secret: label = 1 iff n >= 23
SEED = Artifact({"classifier.py": "T = 5\n\n\ndef classify(n):\n    return int(n >= T)\n"})


class HookedAgent:
    """Calls ``before()`` and then delegates each attempt to ``inner``."""

    def __init__(self, inner, before):
        self.inner, self.before = inner, before

    def attempt(self, ctx, *, seed):
        self.before()
        return self.inner.attempt(ctx, seed=seed)


def test_pareto_objective_selects_in_the_loop():
    res = run(SyntheticDomain(SyntheticConfig(seed=2)),
              config=Config(rounds=3, W=3, branch_count=4, refine_count=3, M=3, objective="pareto",
                            sandbox="inprocess", seed=2))
    phases = [r["dream"] for r in res.trajectory if "dream" in r]
    assert phases
    for ph in phases:
        assert all(math.isfinite(v) for v in ph["values"])
        assert ph["values"][ph["selected"]] == pytest.approx(max(ph["values"]))


def test_manifest_reports_actually_opened_width_and_depth():
    res = run(SyntheticDomain(SyntheticConfig(seed=0)),
              config=Config(rounds=1, W=4, branch_count=6, refine_count=4, dream=False, sandbox="inprocess"),
              initial_policy=rules(open=2, max_rounds=2))
    man, world = res.meta["manifests"][0], res.meta["worlds"][0]
    assert man["planned_grid"]["branch_count"] == 6
    assert man["opened_width"] == len(world.branches()) == 2
    assert man["max_depth"] == max(n.attempt for n in world.non_root()) == 1


def test_adaptive_plan_grid_widens_and_trims_depth_when_depth_stalls():
    hist = [{"iteration": 1, "planned_grid": {"branch_count": 4, "refine_count": 4}, "final_best": 0.5,
             "gain_early": 0.9, "gain_late": 0.1, "max_depth": 2, "hard_fail_frac": 0.0}]
    plan, err = InProcessSession(template_code("adaptive")).plan_grid({}, GridPlanningContext(
        history=hist, fallback_branch_count=4, fallback_refine_count=4, hard_max_branch_count=12,
        hard_max_refine_count=12))
    assert err is None and (plan.branch_count, plan.refine_count) == (5, 3) and "widen" in plan.reason


def test_guard_exposes_structural_meta_of_revealed_cells_only(synthetic_world):
    q = ReplayQuestion(synthetic_world(4, W=3, refine_count=2), 3, root_mode="addressable")
    proxy = QuestionProxy(PrefixGuard(q).transport)
    proxy.reset()
    root = proxy.legal_roots()[0]
    tags = proxy.meta(root).tags
    assert tags.get("direction")
    proxy.probe_batch([root])
    assert proxy.meta(root).tags == tags  # still visible once revealed
    with pytest.raises(GuardViolation):
        proxy.meta("b0.a2")  # neither revealed nor legal


def test_subprocess_policy_timeout_excludes_parent_side_agent_time():
    dom = SyntheticDomain(SyntheticConfig(seed=1))
    cfg = Config(rounds=1, W=1, branch_count=2, refine_count=1, dream=False, sandbox="subprocess",
                 policy_timeout_s=1.5, agent_workers=1)
    row = run(dom, config=cfg, agent=HookedAgent(dom.mock_agent(), lambda: time.sleep(0.6))).trajectory[0]
    assert row["online_error"] is None
    assert row["N"] == 4 and row["calls"] == 4  # 4 x 0.6 s of agent time > the 1.5 s policy timeout


def test_call_budget_truncation_records_the_executed_batch():
    res = run(SyntheticDomain(SyntheticConfig(seed=3)),
              config=Config(rounds=3, W=4, branch_count=4, refine_count=3, dream=False, sandbox="inprocess",
                            max_calls=6))
    man, world = res.meta["manifests"][0], res.meta["worlds"][0]
    assert man["batch_sizes"] == [4, 2]
    assert sum(man["batch_sizes"]) == man["probe_work"] == world.size == 6
    assert world.meta["truncated_batch"]["executed"] == world.meta["truncated_batch"]["requested"][:2]
    assert res.stop_reason == "max_calls" and len(res.trajectory) == 1


def _threshold_domain(log: list) -> FunctionDomain:
    """A new problem: tune the threshold of a classifier program."""
    tasks = [Task(f"n{i}", i, int(i >= HIDDEN_T), family="thr") for i in range(40)]
    splits = {"evolve": [f"n{i}" for i in range(0, 40, 2)], "holdout": [f"n{i}" for i in range(1, 40, 2)]}

    def execute(art, task, seed, llm):
        log.append(task.id)
        ns: dict = {}
        exec(art["classifier.py"], ns)  # noqa: S102 - test domain, mock-agent code
        return int(ns["classify"](task.input))

    def grade(task, out):
        return (1.0, "ok") if out == task.target else (0.0, f"wrong label for {task.input}")

    return FunctionDomain(TaskSuite(tasks, splits, name="thr"), execute, grade, name="threshold",
                          description="classifier.py: classify(n) -> 0/1; accuracy is the score")


def _agent_llm() -> MockLLM:
    """Reads the parent program from the Listing-1 prompt; each branch direction is a different edit."""
    def respond(prompt, system, seed, i):
        assert "You must read every historical proposal" in prompt
        cur = prompt.split("--- CURRENT ARTIFACT ---", 1)[1]
        t = int(re.search(r"^T = (-?\d+)", cur, re.M).group(1))
        m = re.search(r"Direction assigned to this branch: (\w+)", prompt)
        d = m.group(1) if m else "up"
        rng = random.Random(seed)
        new = t + {"up": rng.randint(1, 6), "down": -rng.randint(1, 6), "jump": rng.randint(-10, 10)}[d]
        head = json.dumps({"change": f"{d}: T {t} -> {new}", "hypothesis": "move the decision threshold",
                           "components": ["program"]})
        return (f"```json\n{head}\n```\n=== FILE: classifier.py ===\nT = {new}\n\n\n"
                f"def classify(n):\n    return int(n >= T)\n")

    return MockLLM(respond, name="mock-agent-llm")


def _threshold_task(dom: FunctionDomain) -> DomainTask:
    return DomainTask(dom, SEED, directions=["up", "down", "jump"], workers=1)


def test_generic_new_function_domain_through_llm_paths():
    log: list = []
    dom = _threshold_domain(log)
    cfg = Config(rounds=3, W=3, branch_count=3, refine_count=2, M=2, sandbox="subprocess", seed=7)
    res = run(_threshold_task(dom), llm_propose=_agent_llm(), llm_develop=mock_developer_llm(), config=cfg)
    assert res.meta["seed_score"] == pytest.approx(0.55)  # T=5 on the even ids: 11/20 correct
    assert res.meta["best_score"] > res.meta["seed_score"]
    best_t = int(re.search(r"^T = (-?\d+)", res.best["classifier.py"], re.M).group(1))
    assert abs(best_t - HIDDEN_T) < abs(5 - HIDDEN_T)
    # the sealed holdout split never influenced the run
    assert dom.tasks.is_sealed("holdout") and not any(int(t[1:]) % 2 for t in log)
    with pytest.raises(SealedSplitError):
        dom.tasks.split("holdout")
    rep = transfer_report(dom, None, {"seed": SEED, "best": res.best}, splits=("evolve", "holdout"), workers=1)
    assert rep["splits"]["holdout"]["best"]["S"] > rep["splits"]["holdout"]["seed"]["S"]
    assert res.usage["agent"]["calls"] == res.usage["_cost"]["agent_calls"] > 0
    assert res.usage["developer"]["calls"] == res.usage["_cost"]["developer_calls"] > 0
    phase = res.trajectory[0]["dream"]  # every revision passed the static check and was replayed in the sandbox
    assert len(phase["values"]) == 2 and all(math.isfinite(v) for v in phase["values"])
    res2 = run(_threshold_task(dom), llm_propose=_agent_llm(), llm_develop=mock_developer_llm(), config=cfg)
    assert [r["calls"] for r in res2.trajectory] == [r["calls"] for r in res.trajectory]
    assert res2.meta["best_score"] == res.meta["best_score"] and res2.meta["policy"] == res.meta["policy"]


def test_same_llm_for_two_roles_is_metered_once():
    dev, agent = mock_developer_llm(), _agent_llm()

    def both(prompt, system, seed, i):
        llm = dev if "prefix-only exploration policy" in prompt else agent
        return llm.responder(prompt, system, seed, i)

    shared = MockLLM(both, name="shared")
    res = run(_threshold_task(_threshold_domain([])), llm_propose=shared, llm_develop=shared,
              config=Config(rounds=2, W=3, branch_count=3, refine_count=1, M=2, sandbox="inprocess"))
    assert res.usage["agent"]["calls"] == res.usage["_cost"]["agent_calls"]
    assert res.usage["developer"]["calls"] == res.usage["_cost"]["developer_calls"] == 1


def test_agent_crash_is_a_typed_failed_attempt_not_a_policy_error():
    calls = itertools.count(1)

    def outage():
        if next(calls) % 3 == 0:
            raise ConnectionError("backend down")

    dom = SyntheticDomain(SyntheticConfig(seed=6))
    res = run(dom, config=Config(rounds=2, W=2, branch_count=3, refine_count=1, M=2, sandbox="subprocess",
                                 agent_workers=1), agent=HookedAgent(dom.mock_agent(), outage))
    assert all(r["online_error"] is None for r in res.trajectory)
    nodes = [n for w in res.meta["worlds"] for n in w.non_root()]
    crashed = [n for n in nodes if n.fail_class == "env_error" and "backend down" in (n.error or "")]
    assert crashed and all(not n.success and n.score is None for n in crashed)
    assert res.usage["_cost"]["agent_calls"] == len(nodes)
