"""Adversarial-review regressions for Dream-RSI and the genericity audit.

* regressions for bugs found in review: the Pareto objective in the loop, manifest width/depth,
  structural meta of revealed cells behind the guard, the subprocess policy timeout during slow
  online rollouts, call-budget truncation bookkeeping, LLM usage double counting;
* genericity: a brand-new tiny ``FunctionDomain`` (never seen by the method) driven end to end
  through the LLM paths (``EditorAgent`` with the Listing-1 prompt, ``LLMPolicyDeveloper`` with
  the Listing-2 prompt) using deterministic mock LLMs, with the policy in the subprocess sandbox
  and a sealed holdout split that the loop must never touch.
"""
import json
import math
import re
import time

import pytest

from rsi.core import Artifact, FunctionDomain, MockLLM, SealedSplitError, Task, TaskSuite, transfer_report
from rsi.domains.discovery import SyntheticConfig, SyntheticDomain
from rsi.dream import (Config, DomainTask, GridPlanningContext, GuardViolation, PrefixGuard, ReplayQuestion,
                       mock_developer_llm, rules, run, template_code)
from rsi.dream.guard import InProcessSession
from rsi.dream.policy_api import QuestionProxy


# ----------------------------------------------------------------------------- regressions
def test_pareto_objective_selects_in_the_loop():
    """objective="pareto" used to score every version -inf (the sweep was skipped)."""
    res = run(SyntheticDomain(SyntheticConfig(seed=2)),
              config=Config(rounds=3, W=3, branch_count=4, refine_count=3, M=3, objective="pareto",
                            sandbox="inprocess", seed=2))
    phases = [r["dream"] for r in res.trajectory if "dream" in r]
    assert phases
    for ph in phases:
        vals = ph["values"]
        assert all(math.isfinite(v) for v in vals)
        assert vals[ph["selected"]] == pytest.approx(max(vals))


def test_manifest_reports_actually_opened_width_and_depth():
    """opened_width / max_depth used to echo the plan (support fields), which silently disabled the
    adaptive plan_grid's "widen, trim depth" rule."""
    res = run(SyntheticDomain(SyntheticConfig(seed=0)),
              config=Config(rounds=1, W=4, branch_count=6, refine_count=4, dream=False, sandbox="inprocess"),
              initial_policy=rules(open=2, max_rounds=2))
    man, world = res.meta["manifests"][0], res.meta["worlds"][0]
    assert man["planned_grid"]["branch_count"] == 6
    assert man["opened_width"] == len(world.branches()) == 2
    assert man["max_depth"] == max(n.attempt for n in world.non_root()) == 1


def test_adaptive_plan_grid_widens_and_trims_depth_when_depth_stalls():
    sess = InProcessSession(template_code("adaptive"))
    hist = [{"iteration": 1, "planned_grid": {"branch_count": 4, "refine_count": 4}, "final_best": 0.5,
             "gain_early": 0.9, "gain_late": 0.1, "max_depth": 2, "hard_fail_frac": 0.0}]
    plan, err = sess.plan_grid({}, GridPlanningContext(history=hist, fallback_branch_count=4,
                                                       fallback_refine_count=4, hard_max_branch_count=12,
                                                       hard_max_refine_count=12))
    assert err is None
    assert (plan.branch_count, plan.refine_count) == (5, 3)
    assert "widen" in plan.reason


def test_guard_exposes_structural_meta_of_revealed_cells_only():
    res = run(SyntheticDomain(SyntheticConfig(seed=4)),
              config=Config(rounds=1, W=3, branch_count=4, refine_count=2, dream=False, sandbox="inprocess"))
    q = ReplayQuestion(res.meta["worlds"][0], 3, root_mode="addressable")
    proxy = QuestionProxy(PrefixGuard(q).transport)
    proxy.reset()
    root = proxy.legal_roots()[0]
    tags = proxy.meta(root).tags
    assert tags.get("direction")
    proxy.probe_batch([root])
    assert proxy.meta(root).tags == tags          # still visible once revealed (was {} before the fix)
    with pytest.raises(GuardViolation):
        proxy.meta("b0.a2")                        # neither revealed nor legal: never exposed


class _SlowAgent:
    def __init__(self, inner, delay):
        self.inner, self.delay = inner, delay

    def attempt(self, ctx, *, seed):
        time.sleep(self.delay)
        return self.inner.attempt(ctx, seed=seed)


def test_subprocess_policy_timeout_excludes_parent_side_agent_time():
    """The sandbox timeout used to include the agent calls an online probe triggers, so any live
    search longer than policy_timeout_s depended on a 10 ms race."""
    dom = SyntheticDomain(SyntheticConfig(seed=1))
    cfg = Config(rounds=1, W=1, branch_count=2, refine_count=1, dream=False, sandbox="subprocess",
                 policy_timeout_s=1.5, agent_workers=1)
    res = run(dom, config=cfg, agent=_SlowAgent(dom.mock_agent(), 0.6))
    row = res.trajectory[0]
    assert row["online_error"] is None
    assert row["N"] == 4 and row["calls"] == 4      # 4 x 0.6 s of agent time > 1.5 s policy timeout


def test_call_budget_truncation_records_the_executed_batch():
    res = run(SyntheticDomain(SyntheticConfig(seed=3)),
              config=Config(rounds=3, W=4, branch_count=4, refine_count=3, dream=False, sandbox="inprocess",
                            max_calls=6))
    man, world = res.meta["manifests"][0], res.meta["worlds"][0]
    assert man["batch_sizes"] == [4, 2]
    assert sum(man["batch_sizes"]) == man["probe_work"] == world.size == 6
    assert world.meta["truncated_batch"]["executed"] == world.meta["truncated_batch"]["requested"][:2]
    assert res.stop_reason == "max_calls" and len(res.trajectory) == 1


# --------------------------------------------------------------------- genericity audit
HIDDEN_T = 23      # the grader's secret: label = 1 iff n >= 23


def _threshold_domain(log: list):
    """A NEW problem the method has never seen: tune a threshold classifier (a program)."""
    tasks = [Task(f"n{i}", i, int(i >= HIDDEN_T), family="thr") for i in range(40)]
    evolve = [f"n{i}" for i in range(0, 40, 2)]
    holdout = [f"n{i}" for i in range(1, 40, 2)]

    def execute(art, task, seed, llm):
        log.append(task.id)
        ns: dict = {}
        exec(art["classifier.py"], ns)  # noqa: S102 - test domain, mock-agent code
        return int(ns["classify"](task.input))

    def grade(task, out):
        return (1.0, "ok") if out == task.target else (0.0, f"wrong label for {task.input}")

    return FunctionDomain(TaskSuite(tasks, {"evolve": evolve, "holdout": holdout}, name="thr"), execute, grade,
                          name="threshold", description="classifier.py: classify(n) -> 0/1; accuracy is the score")


SEED = Artifact({"classifier.py": "T = 5\n\n\ndef classify(n):\n    return int(n >= T)\n"})


def _agent_llm() -> MockLLM:
    """Mock discovery LLM: reads the parent program from the Listing-1 prompt and answers in the
    RewriteEditor format; each branch direction is a different edit mechanism."""
    import random

    def respond(prompt, system, seed, i):
        assert "You must read every historical proposal" in prompt       # Listing 1
        cur = prompt.split("--- CURRENT ARTIFACT ---", 1)[1]
        t = int(re.search(r"^T = (-?\d+)", cur, re.M).group(1))
        m = re.search(r"Direction assigned to this branch: (\w+)", prompt)
        d = m.group(1) if m else "up"
        rng = random.Random(seed)
        step = {"up": rng.randint(1, 6), "down": -rng.randint(1, 6), "jump": rng.randint(-10, 10)}[d]
        new = t + step
        head = json.dumps({"change": f"{d}: T {t} -> {new}", "hypothesis": "move the decision threshold",
                           "components": ["program"]})
        return (f"```json\n{head}\n```\n=== FILE: classifier.py ===\nT = {new}\n\n\n"
                f"def classify(n):\n    return int(n >= T)\n")

    return MockLLM(respond, name="mock-agent-llm")


def test_generic_new_function_domain_through_llm_paths():
    log: list = []
    dom = _threshold_domain(log)
    task = DomainTask(dom, SEED, directions=["up", "down", "jump"], workers=1)
    agent_llm, dev_llm = _agent_llm(), mock_developer_llm()
    cfg = Config(rounds=3, W=3, branch_count=3, refine_count=2, M=2, sandbox="subprocess", seed=7)
    res = run(task, llm_propose=agent_llm, llm_develop=dev_llm, config=cfg)
    # the method improved a problem it has never seen, graded only by the domain's locked grader
    assert res.meta["seed_score"] == pytest.approx(0.55)      # T=5 on the even ids: 11/20 correct
    assert res.meta["best_score"] > res.meta["seed_score"]
    best_t = int(re.search(r"^T = (-?\d+)", res.best["classifier.py"], re.M).group(1))
    assert abs(best_t - HIDDEN_T) < abs(5 - HIDDEN_T)
    # the sealed holdout split never influenced the run
    assert dom.tasks.is_sealed("holdout")
    assert not any(int(t[1:]) % 2 for t in log)
    with pytest.raises(SealedSplitError):
        dom.tasks.split("holdout")
    rep = transfer_report(dom, None, {"seed": SEED, "best": res.best}, splits=("evolve", "holdout"), workers=1)
    assert rep["splits"]["holdout"]["best"]["S"] > rep["splits"]["holdout"]["seed"]["S"]
    # LLM usage is metered per role and matches the cost meter
    assert res.usage["agent"]["calls"] == res.usage["_cost"]["agent_calls"] > 0
    assert res.usage["developer"]["calls"] == res.usage["_cost"]["developer_calls"] > 0
    # every developer revision went through the static check and was replayed in the sandbox
    phase = res.trajectory[0]["dream"]
    assert len(phase["values"]) == 2 and all(math.isfinite(v) for v in phase["values"])
    # deterministic offline
    res2 = run(task.__class__(dom, SEED, directions=["up", "down", "jump"], workers=1), llm_propose=_agent_llm(),
               llm_develop=mock_developer_llm(), config=cfg)
    assert [r["calls"] for r in res2.trajectory] == [r["calls"] for r in res.trajectory]
    assert res2.meta["best_score"] == res.meta["best_score"]
    assert res2.meta["policy"] == res.meta["policy"]


def test_same_llm_for_two_roles_is_metered_once():
    dev = mock_developer_llm()
    agent = _agent_llm()

    def both(prompt, system, seed, i):
        if "prefix-only exploration policy" in prompt:
            return dev.responder(prompt, system, seed, i)
        return agent.responder(prompt, system, seed, i)

    shared = MockLLM(both, name="shared")
    dom = _threshold_domain([])
    task = DomainTask(dom, SEED, directions=["up", "down", "jump"], workers=1)
    res = run(task, llm_propose=shared, llm_develop=shared,
              config=Config(rounds=2, W=3, branch_count=3, refine_count=1, M=2, sandbox="inprocess"))
    assert res.usage["agent"]["calls"] == res.usage["_cost"]["agent_calls"]
    assert res.usage["developer"]["calls"] == res.usage["_cost"]["developer_calls"] == 1


class _FlakyAgent:
    """Raises on every third call (a backend outage), otherwise delegates."""

    def __init__(self, inner):
        self.inner, self.n = inner, 0

    def attempt(self, ctx, *, seed):
        self.n += 1
        if self.n % 3 == 0:
            raise ConnectionError("backend down")
        return self.inner.attempt(ctx, seed=seed)


def test_agent_crash_is_a_typed_failed_attempt_not_a_policy_error():
    """An exception inside an online probe used to be blamed on the policy (in-process) or to crash
    the whole run (subprocess sandbox)."""
    dom = SyntheticDomain(SyntheticConfig(seed=6))
    res = run(dom, config=Config(rounds=2, W=2, branch_count=3, refine_count=1, M=2, sandbox="subprocess",
                                 agent_workers=1), agent=_FlakyAgent(dom.mock_agent()))
    assert all(r["online_error"] is None for r in res.trajectory)
    nodes = [n for w in res.meta["worlds"] for n in w.non_root()]
    crashed = [n for n in nodes if n.fail_class == "env_error" and "backend down" in (n.error or "")]
    assert crashed and all(not n.success and n.score is None for n in crashed)
    assert res.usage["_cost"]["agent_calls"] == len(nodes)
