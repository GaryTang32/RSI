"""Dream-RSI core: discovery trees, replay semantics, guard, objectives and the overview demo."""
import json

import pytest

from rsi.core import Ledger, Node
from rsi.dream import (DEMO_EXPECTED, BatchError, Config, DiscoveryNode, DiscoveryTree, Eq1Objective, GridPlan,
                       GuardViolation, ParetoSweepObjective, PrefixGuard, QuestionProxy, ReplayEvaluator,
                       ReplayQuestion, adaptive, code_of, demo_tree, parallel_refine, rules, run_demo, static_check,
                       template_code)
from rsi.dream.guard import InProcessRunner, SubprocessRunner
from rsi.dream.tree import ROOT_ID, cell_id

ORACLE_WORLD = [[0.1, 0.2, 0.3], [0.3, 0.9, 0.2]]
THREE_BRANCHES = [[0.1, 0.5, 0.52], [0.3, 0.31, 0.2], [0.05, 0.4, 0.8]]


def chain_tree(scores_by_branch: list[list[float]], order: list[int] | None = None) -> DiscoveryTree:
    """One chain per branch; ``order`` is the creation order of the branches' first cells."""
    order = order or range(len(scores_by_branch))
    first_seq = {b: i + 1 for i, b in enumerate(order)}
    nodes = [DiscoveryNode(cell_id(b, a), cell_id(b, a - 1) if a else ROOT_ID, b, a,
                           100 * (b + 1) + a if a else first_seq[b], s)
             for b in order for a, s in enumerate(scores_by_branch[b])]
    return DiscoveryTree(DiscoveryNode(ROOT_ID, None, score=0.0), nodes,
                         branch_tags={b: {"direction": f"d{b}"} for b in range(len(scores_by_branch))})


def cells(observations) -> list[str]:
    return [o.cell_id for o in observations]


@pytest.mark.parametrize("runner", ["inprocess", "subprocess"])
def test_demo_scores_and_reveal_maps_match_overview(runner):
    rows = run_demo(runner=runner)
    assert {r.key: round(r.score, 3) for r in rows} == DEMO_EXPECTED
    assert all(r.matches_schedule() for r in rows)
    focus = next(r for r in rows if r.key == "focus")
    assert (focus.best, focus.attempts, focus.rounds) == (0.71, 11, 5)


def test_eq1_hand_computed_toy():
    ev = ReplayEvaluator(Eq1Objective(0.01, 0.005, normalize=False), W=3, fallback=(3, 4), runner="inprocess")
    rep = ev.evaluate(code_of(parallel_refine()), [demo_tree()])
    e = rep.episodes[0]
    assert (e.N, e.k, e.best) == (15, 5, 0.71)
    assert rep.value == pytest.approx(0.71 - 0.15 + 0.005 * 3)


def test_root_opens_earliest_created_unrevealed_branch():
    tree = chain_tree([[0.1, 0.2], [0.3], [0.5, 0.6]], order=[2, 0, 1])
    q = ReplayQuestion(tree, W=3, root_mode="earliest")
    assert cells(q.probe_batch(["b1.a0"])) == ["b2.a0"]  # any root cell opens the earliest-created branch
    assert cells(q.probe_batch(["b1.a0"])) == ["b0.a0"]
    assert cells(ReplayQuestion(tree, W=3, root_mode="addressable").probe_batch(["b1.a0"])) == ["b1.a0"]


def test_exhausted_leaf_reveals_nothing_but_counts_a_round():
    tree = chain_tree([[0.1, 0.2], [0.3]])
    q = ReplayQuestion(tree, W=2, plan=GridPlan(2, 3), root_mode="addressable")
    assert q.out_of_support is True
    q.probe_batch(["b0.a0", "b1.a0"])
    assert "b1.a1" in q.legal_actions()  # plan clipped to the support's depth 1; b1 has no recorded attempt 1
    k0, n0 = q.k, q.N
    assert q.probe_batch(["b1.a1"]) == [] and q.k == k0 + 1 and q.N == n0
    assert "b1.a1" not in q.legal_actions()
    qh = ReplayQuestion(tree, W=2, root_mode="addressable", hide_missing=True)
    qh.probe_batch(["b0.a0", "b1.a0"])
    assert qh.legal_actions() == ["b0.a1"]


def test_batch_legality():
    q = ReplayQuestion(chain_tree([[0.1, 0.2, 0.3], [0.3, 0.4]]), W=2, root_mode="addressable")
    for batch in (["b0.a0", "b0.a0"], ["b0.a0", "b1.a0", "b1.a0"], ["b0.a1"]):
        with pytest.raises(BatchError):
            q.probe_batch(batch)
    msg = q.validate_batch(["b0.a0", "b0.a1"])
    assert "parent and its child" in msg or "illegal" in msg
    q.probe_batch(["b0.a0"])
    assert q.validate_batch(["b0.a1", "b0.a2"]) is not None


def test_replay_deterministic_and_zero_agent_calls():
    tree = chain_tree(THREE_BRANCHES)
    ev = ReplayEvaluator(W=2, fallback=(3, 2), runner="inprocess")
    a, b = (ev.evaluate(code_of(adaptive()), [tree, tree]) for _ in range(2))
    assert a.value == b.value and a.per_world == b.per_world
    assert [e.revealed for e in a.episodes] == [e.revealed for e in b.episodes]


def test_proxy_blocks_hidden_state_in_process():
    g = PrefixGuard(ReplayQuestion(chain_tree([[0.1, 0.2], [0.3, 0.4]]), W=2))
    p = QuestionProxy(g.transport)
    p.reset()
    for peek in (lambda: p.best_so_far, lambda: p.budget_spent, lambda: p.tree, lambda: p.meta("b0.a1")):
        with pytest.raises(GuardViolation):
            peek()
    assert len(g.violations) == 4
    p.probe_batch(p.legal_roots())
    assert set(p.observed()) == {"b0.a0", "b1.a0"}
    assert all(o.score is not None for o in p.observed().values())


@pytest.mark.parametrize("runner", [InProcessRunner(), SubprocessRunner(timeout_s=20)])
def test_oracle_policy_is_disqualified_behind_guard(runner):
    tree = chain_tree(ORACLE_WORLD)
    ev = ReplayEvaluator(W=2, fallback=(2, 2), runner=runner, root_mode="addressable")
    rep = ev.evaluate(template_code("oracle"), [tree])
    e = rep.episodes[0]
    # quality floor 0 - beta1 * |world| - 1: below every honest episode
    assert e.disqualified and e.violations and rep.value == pytest.approx(0.0 - 0.01 * 6 - 1.0)
    assert rep.value < ev.evaluate(code_of(parallel_refine()), [tree]).value


def test_oracle_inflated_without_guard():
    tree = chain_tree(ORACLE_WORLD)
    kw = {"W": 2, "fallback": (2, 2), "runner": "inprocess", "root_mode": "addressable"}
    v_cheat = ReplayEvaluator(**kw, unguarded=True).evaluate(template_code("oracle"), [tree]).value
    v_honest = ReplayEvaluator(**kw).evaluate(code_of(parallel_refine()), [tree]).value
    assert v_cheat > v_honest and v_cheat == pytest.approx(1.0 - 0.02 + 0.005)


def test_sandbox_cannot_open_files(tmp_path):
    secret = tmp_path / "secret.json"
    secret.write_text("{}")
    code = template_code("parallel_refine").replace(
        "    def solve(self, question, budget=None):\n",
        f"    def solve(self, question, budget=None):\n        open({str(secret)!r}).read()\n", 1)
    ev = ReplayEvaluator(W=2, fallback=(2, 1), runner=SubprocessRunner(timeout_s=20))
    error = ev.evaluate(code, [chain_tree([[0.1, 0.2], [0.3, 0.4]])]).episodes[0].error
    assert error and ("Errno" in error or "Error" in error)


def test_static_check():
    assert static_check(template_code("adaptive")).ok and static_check(template_code("parallel_refine")).ok
    bad = static_check(template_code("oracle"))
    assert not bad.ok and any("best_so_far" in e for e in bad.errors)
    refine = template_code("parallel_refine")
    for code, word in ((refine.replace("def plan_grid", "def _plan_grid"), "plan_grid"),
                       ("import os\n" + template_code("adaptive"), "import"),
                       (refine.replace("        return GridPlan(", "        return None\n        GridPlan("), "None")):
        assert any(word in e for e in static_check(code).errors)


def test_normalized_eq1_and_pareto_sweep():
    tree = chain_tree(THREE_BRANCHES)
    ev = ReplayEvaluator(ParetoSweepObjective(beta_grid=(0.1, 0.5, 1.0)), W=3, fallback=(3, 2), runner="inprocess")
    sw = ev.evaluate(code_of(adaptive()), [tree]).sweep
    assert {p["beta"] for p in sw["points"]} == {0.1, 0.5, 1.0}
    assert 0.0 <= sw["auc"] <= 1.0 and 1 / 3 - 1e-9 <= sw["parallel_penalty"] <= 1.0
    assert sw["reward"] == pytest.approx(sw["auc"] - 0.1 * sw["parallel_penalty"])
    ns = [p["N"] for p in sorted(sw["points"], key=lambda p: p["beta"])]
    assert ns == sorted(ns)  # a higher beta never probes less
    full = ReplayEvaluator(Eq1Objective(), W=3, fallback=(3, 2), runner="inprocess").evaluate(
        code_of(parallel_refine()), [tree])
    assert full.episodes[0].attainment == 1.0
    assert full.value == pytest.approx(1.0 - 0.01 * 9 + 0.005 * 3)


def test_no_reward_for_out_of_support_plans():
    tree = chain_tree([[0.1, 0.5], [0.3, 0.31]])
    deep = code_of(rules(plan_w=2, plan_r=6))
    clip, nore, dflt = (ReplayEvaluator(obj, W=2, runner="inprocess").evaluate(deep, [tree])
                        for obj in (Eq1Objective(support="clip"), Eq1Objective(support="no_reward"), Eq1Objective()))
    assert clip.episodes[0].out_of_support and clip.value > nore.value
    # Listing 2: an out-of-support plan "cannot earn replay reward"
    assert Eq1Objective().support == Config().support == "no_reward" and dflt.value == pytest.approx(nore.value)


def test_tree_json_roundtrip_and_ledger_conversion(tmp_path):
    led = Ledger()
    for node in (Node("base", None, score=0.2, kind="baseline"), Node("c1", "base", score=0.3),
                 Node("c2", "c1", score=0.35), Node("c3", "c1", score=0.5),
                 Node("c4", "base", score=None, status="crash")):
        led.add(node)
    tree = DiscoveryTree.from_ledger(led)
    assert tree.size == 4 and tree.root_score == 0.2
    assert [n.diagnostics["ledger_id"] for n in tree.branches()[0]] == ["c1", "c2"]
    fork_b = next(b for b, t in tree.branch_tags.items() if "fork_of" in t)
    assert tree.branch_tags[fork_b]["fork_of"] == "b0.a0"
    q = ReplayQuestion(tree, W=4, root_mode="addressable")
    assert cell_id(fork_b, 0) not in q.legal_roots()  # a fork opens only after its fork point
    q.probe_batch(q.legal_roots())
    assert cell_id(fork_b, 0) in q.legal_roots()
    assert not next(n for n in tree.non_root() if n.diagnostics["ledger_id"] == "c4").success
    back = DiscoveryTree.load(tree.save(tmp_path / "t.json"))
    assert json.dumps(back.to_json(), sort_keys=True) == json.dumps(tree.to_json(), sort_keys=True)
    assert len(back.to_ledger()) == len(back)
    assert DiscoveryTree.from_ledger(led, lower_is_better=True).root_score == -0.2
