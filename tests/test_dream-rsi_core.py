"""Dream-RSI core: trees, replay semantics, guard, objectives, demo (spec §9.1 invariants a-g)."""
import json

import pytest

from rsi.core import Ledger, Node
from rsi.dream import (DEMO_EXPECTED, BatchError, DiscoveryNode, DiscoveryTree, Eq1Objective, GridPlan,
                       ParetoSweepObjective, PrefixGuard, QuestionProxy, ReplayEvaluator, ReplayQuestion,
                       GuardViolation, adaptive, code_of, demo_tree, parallel_refine, rules, run_demo, static_check,
                       template_code)
from rsi.dream.guard import InProcessRunner, SubprocessRunner
from rsi.dream.tree import ROOT_ID, cell_id


def chain_tree(scores_by_branch, root=0.0, order=None):
    """Branches b with the given score lists; ``order`` = creation order of the branches' first cells."""
    root_n = DiscoveryNode(ROOT_ID, None, score=root)
    nodes, seq = [], 0
    order = order or list(range(len(scores_by_branch)))
    firsts = {}
    for b in order:
        seq += 1
        firsts[b] = seq
    for b in order:
        for a, s in enumerate(scores_by_branch[b]):
            nseq = firsts[b] if a == 0 else 100 * (b + 1) + a
            nodes.append(DiscoveryNode(cell_id(b, a), ROOT_ID if a == 0 else cell_id(b, a - 1), b, a, nseq, s))
    return DiscoveryTree(root_n, nodes, branch_tags={b: {"direction": f"d{b}"} for b in range(len(scores_by_branch))})


# ------------------------------------------------------------------ (g) the overview demo
def test_demo_scores_and_reveal_maps_match_overview():
    rows = run_demo()
    got = {r.key: round(r.score, 3) for r in rows}
    assert got == DEMO_EXPECTED
    assert all(r.matches_schedule() for r in rows)
    focus = next(r for r in rows if r.key == "focus")
    assert (focus.best, focus.attempts, focus.rounds) == (0.71, 11, 5)


def test_demo_in_sandbox_matches():
    rows = run_demo(runner="subprocess")
    assert {r.key: round(r.score, 3) for r in rows} == DEMO_EXPECTED


def test_eq1_hand_computed_toy():
    ev = ReplayEvaluator(Eq1Objective(0.01, 0.005, normalize=False), W=3, fallback=(3, 4), runner="inprocess")
    rep = ev.evaluate(code_of(parallel_refine()), [demo_tree()])
    e = rep.episodes[0]
    assert (e.N, e.k, e.best) == (15, 5, 0.71)
    assert rep.value == pytest.approx(0.71 - 0.15 + 0.005 * 3)


# ------------------------------------------------------------------ replay semantics
def test_root_opens_earliest_created_unrevealed_branch():
    # branch 2 was created first, then 0, then 1
    tree = chain_tree([[0.1, 0.2], [0.3], [0.5, 0.6]], order=[2, 0, 1])
    q = ReplayQuestion(tree, W=3, root_mode="earliest")
    obs = q.probe_batch(["b1.a0"])          # any root cell -> earliest-created unrevealed branch
    assert [o.cell_id for o in obs] == ["b2.a0"]
    obs = q.probe_batch(["b1.a0"])
    assert [o.cell_id for o in obs] == ["b0.a0"]
    qa = ReplayQuestion(tree, W=3, root_mode="addressable")
    assert [o.cell_id for o in qa.probe_batch(["b1.a0"])] == ["b1.a0"]


def test_exhausted_leaf_reveals_nothing_but_counts_a_round():
    tree = chain_tree([[0.1, 0.2], [0.3]])
    q = ReplayQuestion(tree, W=2, plan=GridPlan(2, 3), root_mode="addressable")
    assert q.out_of_support is True
    q.probe_batch(["b0.a0", "b1.a0"])
    # the plan is clipped to the support (depth 1); b1 has no recorded attempt 1
    assert "b1.a1" in q.legal_actions()
    k0, n0 = q.k, q.N
    out = q.probe_batch(["b1.a1"])
    assert out == [] and q.k == k0 + 1 and q.N == n0
    assert "b1.a1" not in q.legal_actions()   # exhausted
    qh = ReplayQuestion(tree, W=2, root_mode="addressable", hide_missing=True)
    qh.probe_batch(["b0.a0", "b1.a0"])
    assert qh.legal_actions() == ["b0.a1"]


def test_batch_legality():
    tree = chain_tree([[0.1, 0.2, 0.3], [0.3, 0.4]])
    q = ReplayQuestion(tree, W=2, root_mode="addressable")
    with pytest.raises(BatchError):
        q.probe_batch(["b0.a0", "b0.a0"])
    with pytest.raises(BatchError):
        q.probe_batch(["b0.a0", "b1.a0", "b1.a0"][:3])
    with pytest.raises(BatchError):
        q.probe_batch(["b0.a1"])              # not legal before its parent is revealed
    assert "parent and its child" in q.validate_batch(["b0.a0", "b0.a1"]) or "illegal" in q.validate_batch(
        ["b0.a0", "b0.a1"])
    q.probe_batch(["b0.a0"])
    assert q.validate_batch(["b0.a1", "b0.a2"]) is not None


def test_replay_deterministic_and_zero_agent_calls():
    tree = chain_tree([[0.1, 0.5, 0.52], [0.3, 0.31, 0.2], [0.05, 0.4, 0.8]])
    ev = ReplayEvaluator(W=2, fallback=(3, 2), runner="inprocess")
    code = code_of(adaptive())
    a = ev.evaluate(code, [tree, tree])
    b = ev.evaluate(code, [tree, tree])
    assert a.value == b.value and a.per_world == b.per_world
    assert [e.revealed for e in a.episodes] == [e.revealed for e in b.episodes]


# ------------------------------------------------------------------ guard (d)
def test_proxy_blocks_hidden_state_in_process():
    tree = chain_tree([[0.1, 0.2], [0.3, 0.4]])
    q = ReplayQuestion(tree, W=2)
    g = PrefixGuard(q)
    p = QuestionProxy(g.transport)
    p.reset()
    for bad in (lambda: p.best_so_far, lambda: p.budget_spent, lambda: p.tree, lambda: p.meta("b0.a1")):
        with pytest.raises(GuardViolation):
            bad()
    assert len(g.violations) == 4
    p.probe_batch(p.legal_roots())
    assert set(p.observed()) == {"b0.a0", "b1.a0"}
    assert all(o.score is not None for o in p.observed().values())


@pytest.mark.parametrize("runner", [InProcessRunner(), SubprocessRunner(timeout_s=20)])
def test_oracle_policy_is_disqualified_behind_guard(runner):
    tree = chain_tree([[0.1, 0.2, 0.3], [0.3, 0.9, 0.2]])
    ev = ReplayEvaluator(W=2, fallback=(2, 2), runner=runner, root_mode="addressable")
    rep = ev.evaluate(template_code("oracle"), [tree])
    e = rep.episodes[0]
    assert e.disqualified and e.violations and rep.value == -1.0


def test_oracle_inflated_without_guard():
    tree = chain_tree([[0.1, 0.2, 0.3], [0.3, 0.9, 0.2]])
    honest = ReplayEvaluator(W=2, fallback=(2, 2), runner="inprocess", root_mode="addressable")
    cheat = ReplayEvaluator(W=2, fallback=(2, 2), runner="inprocess", root_mode="addressable", unguarded=True)
    v_cheat = cheat.evaluate(template_code("oracle"), [tree]).value
    v_honest = honest.evaluate(code_of(parallel_refine()), [tree]).value
    assert v_cheat > v_honest and v_cheat == pytest.approx(1.0 - 0.02 + 0.005)


def test_sandbox_cannot_open_files(tmp_path):
    secret = tmp_path / "secret.json"
    secret.write_text("{}")
    code = template_code("parallel_refine").replace(
        "    def solve(self, question, budget=None):\n",
        f"    def solve(self, question, budget=None):\n        open({str(secret)!r}).read()\n", 1)
    ev = ReplayEvaluator(W=2, fallback=(2, 1), runner=SubprocessRunner(timeout_s=20))
    rep = ev.evaluate(code, [chain_tree([[0.1, 0.2], [0.3, 0.4]])])
    assert rep.episodes[0].error and ("Errno" in rep.episodes[0].error or "Error" in rep.episodes[0].error)


def test_static_check():
    assert static_check(template_code("adaptive")).ok
    assert static_check(template_code("parallel_refine")).ok
    bad = static_check(template_code("oracle"))
    assert not bad.ok and any("best_so_far" in e for e in bad.errors)
    no_plan = template_code("parallel_refine").replace("def plan_grid", "def _plan_grid")
    assert any("plan_grid" in e for e in static_check(no_plan).errors)
    assert any("import" in e for e in static_check("import os\n" + template_code("adaptive")).errors)
    none_ret = template_code("parallel_refine").replace("        return GridPlan(", "        return None\n        GridPlan(")
    assert any("None" in e for e in static_check(none_ret).errors)


# ------------------------------------------------------------------ objectives
def test_normalized_eq1_and_pareto_sweep():
    tree = chain_tree([[0.1, 0.5, 0.52], [0.3, 0.31, 0.2], [0.05, 0.4, 0.8]])
    ev = ReplayEvaluator(ParetoSweepObjective(beta_grid=(0.1, 0.5, 1.0)), W=3, fallback=(3, 2), runner="inprocess")
    rep = ev.evaluate(code_of(adaptive()), [tree])
    sw = rep.sweep
    assert set(p["beta"] for p in sw["points"]) == {0.1, 0.5, 1.0}
    assert 0.0 <= sw["auc"] <= 1.0 and 1 / 3 - 1e-9 <= sw["parallel_penalty"] <= 1.0
    assert sw["reward"] == pytest.approx(sw["auc"] - 0.1 * sw["parallel_penalty"])
    # higher beta never probes less for the adaptive template on this tree
    ns = [p["N"] for p in sorted(sw["points"], key=lambda p: p["beta"])]
    assert ns == sorted(ns)
    full = ReplayEvaluator(Eq1Objective(), W=3, fallback=(3, 2), runner="inprocess").evaluate(
        code_of(parallel_refine()), [tree])
    assert full.episodes[0].attainment == 1.0
    assert full.value == pytest.approx(1.0 - 0.01 * 9 + 0.005 * 3)


def test_no_reward_for_out_of_support_plans():
    tree = chain_tree([[0.1, 0.5], [0.3, 0.31]])
    deep = code_of(rules(plan_w=2, plan_r=6))
    clip = ReplayEvaluator(Eq1Objective(), W=2, runner="inprocess").evaluate(deep, [tree])
    nore = ReplayEvaluator(Eq1Objective(support="no_reward"), W=2, runner="inprocess").evaluate(deep, [tree])
    assert clip.episodes[0].out_of_support and clip.value > nore.value


# ------------------------------------------------------------------ worlds from any ledger
def test_tree_json_roundtrip_and_ledger_conversion(tmp_path):
    led = Ledger()
    led.add(Node("base", None, score=0.2, kind="baseline"))
    led.add(Node("c1", "base", score=0.3))
    led.add(Node("c2", "c1", score=0.35))
    led.add(Node("c3", "c1", score=0.5))          # fork at c1
    led.add(Node("c4", "base", score=None, status="crash"))
    tree = DiscoveryTree.from_ledger(led)
    assert tree.size == 4 and tree.root_score == 0.2
    br = tree.branches()
    assert [n.diagnostics["ledger_id"] for n in br[0]] == ["c1", "c2"]
    fork_b = next(b for b, t in tree.branch_tags.items() if "fork_of" in t)
    assert tree.branch_tags[fork_b]["fork_of"] == "b0.a0"
    q = ReplayQuestion(tree, W=4, root_mode="addressable")
    assert cell_id(fork_b, 0) not in q.legal_roots()      # fork opens only after its fork point
    q.probe_batch(q.legal_roots())
    assert cell_id(fork_b, 0) in q.legal_roots()
    crash = [n for n in tree.non_root() if n.diagnostics["ledger_id"] == "c4"][0]
    assert not crash.success
    p = tree.save(tmp_path / "t.json")
    back = DiscoveryTree.load(p)
    assert json.dumps(back.to_json(), sort_keys=True) == json.dumps(tree.to_json(), sort_keys=True)
    led2 = back.to_ledger()
    assert len(led2) == len(back)
    lower = DiscoveryTree.from_ledger(led, lower_is_better=True)
    assert lower.root_score == -0.2
