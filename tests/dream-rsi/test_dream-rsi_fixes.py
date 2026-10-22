"""Claims-audit regressions: cross-episode state, Listing 1/2 prompts, round budgets, cost meter, autocorrelation."""
import dataclasses
import gzip
import json
import re
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from rsi.core import Artifact, MockLLM, Usage
from rsi.domains.discovery import AutocorrelationDomain, SumDiffDomain, SyntheticConfig, SyntheticDomain
from rsi.domains.discovery.autocorr import objectives, program
from rsi.dream import (Config, DiscoveryNode, EditorAgent, GridPlan, GridPlanningContext, LLMPolicyDeveloper,
                       ParetoSweepObjective, ReplayEvaluator, ReplayQuestion, mock_developer_llm, run, static_check,
                       template_code)
from rsi.dream.agent import AttemptContext, AttemptRecord, history_records
from rsi.dream.cost import CostMeter
from rsi.dream.developer import developer_prompt
from rsi.dream.guard import SubprocessSession, _load_class

RECORDED_POLICIES = Path(__file__).parent / "data" / "recorded_policies.json.gz"

REFINE = template_code("parallel_refine")
MEMO = template_code("memo_module")
# the memo parked on the imported policy_api module and reached through self: no function names a module global
POLICY_API_MEMO = MEMO.replace(
    "_MEMO = {}", "import policy_api\nif not hasattr(policy_api, '_vfy_memo_sandbox'):\n"
                  "    policy_api._vfy_memo_sandbox = {}\n_MEMO = policy_api._vfy_memo_sandbox").replace(
    '        self.beta = float(self.config.get("beta", 0.6))',
    '        self.beta = float(self.config.get("beta", 0.6))\n        self.memo = _MEMO').replace(
    "target = _MEMO.get(key)", "target = self.memo.get(key)").replace(
    "_MEMO[key] = (best[1], best[2])", "self.memo[key] = (best[1], best[2])")

STATE_POLICY = '''
from policy_api import GridPlan, LLMDesignedMethod, SimResult, _budget_done, _record_curve, finalize_result
NAME = "OptimalPolicy"
_SEEN = []

class OptimalPolicy(LLMDesignedMethod):
    shared = {}

    def plan_grid(self, context):
        return GridPlan(int(context.fallback_branch_count), int(context.fallback_refine_count), "fallback")

    def solve(self, question, budget=None):
        question.reset()
        res = SimResult()
        _SEEN.append(1)
        self.shared["n"] = self.shared.get("n", 0) + 1
        rounds = __ROUNDS__
        k = 0
        while not _budget_done(question, budget) and k < rounds:
            legal = sorted(question.legal_actions())
            if not legal:
                break
            question.probe_batch(legal[:1], on_reveal=lambda _: _record_curve(res, question))
            k += 1
        return finalize_result(question, res)
'''
# one probe per round; the round count leaks module and class state ...
COUNTER_POLICY = STATE_POLICY.replace("__ROUNDS__", "len(_SEEN) + self.shared[\"n\"]")
# ... or a message an earlier episode left in the random module's state
RANDOM_POLICY = ("import random\nMAGIC = random.Random(12345).random()\n" + STATE_POLICY).replace(
    "__ROUNDS__", "5 if abs(random.random() - MAGIC) < 1e-12 else 2").replace(
    "        return finalize_result(question, res)", "        random.seed(12345)\n"
                                                    "        return finalize_result(question, res)")


def _cfg(**kw) -> Config:
    return Config(sandbox="inprocess", trace=False, agent_workers=1, **kw)


@pytest.fixture
def worlds(synthetic_world):
    return [synthetic_world(40 + s, refine_count=3, seed=s, agent_workers=1, trace=False) for s in range(3)]


def _patch(module: str = "", cls: str = "", solve: str = "") -> str:
    """parallel_refine with lines added after ``NAME``, before ``plan_grid`` and at the start of ``solve``."""
    code = REFINE
    if module:
        code = code.replace('NAME = "OptimalPolicy"', 'NAME = "OptimalPolicy"\n' + module)
    if cls:
        code = code.replace("    def plan_grid", cls + "\n\n    def plan_grid")
    if solve:
        code = code.replace("        res = SimResult()", "        res = SimResult()\n" + solve)
    return code


@pytest.mark.parametrize("runner,cheater", [("inprocess", MEMO), ("subprocess", MEMO),
                                            ("subprocess", POLICY_API_MEMO)],
                         ids=["inprocess", "subprocess", "policy_api-subprocess"])
def test_memo_cheater_gains_nothing_across_replay_episodes(worlds, runner, cheater):
    """The cheater remembers each world's best cell and walks to it when the beta sweep revisits the world."""
    ev = ReplayEvaluator(ParetoSweepObjective(), W=4, runner=runner, fallback=(4, 3))
    r_memo, r_pr = ev.evaluate(cheater, worlds), ev.evaluate(REFINE, worlds)
    assert not static_check(cheater).ok and r_memo.disqualified == 0
    assert r_memo.value == pytest.approx(r_pr.value)  # a fresh namespace per episode keeps the memo empty
    assert [e.N for e in r_memo.sweep_episodes] == [e.N for e in r_pr.sweep_episodes]
    shared = ReplayEvaluator(ParetoSweepObjective(), W=4, runner=runner, fallback=(4, 3), fresh_episodes=False)
    assert shared.evaluate(cheater, worlds).value > r_pr.value + 0.1


@pytest.mark.parametrize("runner,policy,shared_probes", [("inprocess", COUNTER_POLICY, [2, 4, 6, 8, 10, 12]),
                                                         ("subprocess", COUNTER_POLICY, [2, 4, 6, 8, 10, 12]),
                                                         ("subprocess", RANDOM_POLICY, [2, 5, 5, 5, 5, 5])],
                         ids=["inprocess", "subprocess", "random-state-subprocess"])
def test_every_replay_episode_starts_from_a_fresh_namespace(worlds, runner, policy, shared_probes):
    def probes(**kw) -> list[int]:  # one world: 1 episode + 5 sweep episodes
        ev = ReplayEvaluator(ParetoSweepObjective(), W=4, runner=runner, fallback=(4, 3), **kw)
        rep = ev.evaluate(policy, worlds[:1])
        return [e.N for e in [*rep.episodes, *rep.sweep_episodes]]

    assert set(probes()) == {2}
    assert probes(fresh_episodes=False) == shared_probes
    assert not static_check(policy).ok


def test_subprocess_session_forks_a_fresh_episode_and_survives_a_crash(worlds):
    code = COUNTER_POLICY.replace("        rounds = ", "        if self.config.get('crash'):\n"
                                  "            raise SystemExit(3)\n        rounds = ")
    with SubprocessSession(code, timeout_s=20) as sess:
        probes = []
        for crash in (False, True, False, False):
            assert sess.begin_episode() is None
            q = ReplayQuestion(worlds[0], 4, GridPlan(4, 3))
            assert (sess.solve({"crash": crash}, q).error is not None) == crash
            probes.append(None if crash else q.N)
        assert probes[0] == probes[2] == probes[3] and sess.episodes == 4


def test_static_check_rejects_state_that_outlives_an_episode():
    stateful = {
        "module dict store": _patch(module="_M = {}", solve="        _M[1] = 2"),
        "module list append": _patch(module="_L = []", solve="        _L.append(1)"),
        "global": _patch(module="_K = 0").replace("        res = SimResult()",
                                                  "        global _K\n        res = SimResult()\n        _K = 1"),
        "imported module attribute": _patch(solve="        math.memo = 1"),
        "class attribute via self": _patch(cls="    cache = {}", solve="        self.cache[1] = 2"),
        "type(self)": _patch(solve="        type(self).x = 1"),
        "__class__": _patch(solve="        self.__class__.x = 1"),
        "lru_cache": REFINE.replace("class OptimalPolicy", "import functools\n\n\n@functools.lru_cache(None)\n"
                                                           "def f(x):\n    return x\n\n\nclass OptimalPolicy"),
        "memo cheater": MEMO,
        "module-level store into an imported module": _patch(module="import math\nmath.memo = {}"),
        "module-level call on an imported module": _patch(module='import policy_api\npolicy_api.__all__.append("x")'),
    }
    for what, code in stateful.items():
        assert not static_check(code).ok, what
    stateless = {
        "templates": template_code("adaptive"),
        "local mutation + closure over an enclosing local": _patch(
            solve="        seen = set()\n        mark = lambda c: seen.add(c)\n        mark(1)\n"
                  "        def inner(x):\n            seen.add(x)\n        inner(2)"),
        "instance cache rebound per instance": _patch(cls="    cache = {}",
                                                      solve="        self.cache = {}\n        self.cache[1] = 2"),
        "read-only module constant": _patch(module='P = {"a": 1}', solve="        x = P.get('a')"),
        "module-level reads and local stores": _patch(module='import math\nTAU = math.pi * 2\nD = {}\nD["a"] = 1'),
    }
    for what, code in stateless.items():
        assert static_check(code).ok, (what, static_check(code).errors)
    for name in ("parallel_refine", "adaptive", "rules", "peek_reset"):
        assert static_check(template_code(name)).ok, name


def test_recorded_llm_policies_still_pass_the_static_check():
    policies = json.loads(gzip.decompress(RECORDED_POLICIES.read_bytes()))
    assert policies
    assert not (bad := [path for path, src in policies.items() if not static_check(src).ok]), bad


# Listing-2 rules quoted from App. B.2
L2_RULES = [
    "Every prune, widen, deepen, batch, and stop decision must be explainable from the current prefix.",
    "Shallow weak scores are not enough to discard a branch: deeper attempts can recover.",
    "A repairable latest failure must not erase its historical successful anchor or by itself cause permanent "
    "starvation.",
    "Output/correctness mismatch, shared-memory/resource limits, and variable/code, mask/layout/shape errors are "
    "normally repairable.",
    "Do not infer algorithmic failure from one such error.",
    "n_valid == 0 and branch_failed_hard(obs) are signals, not unconditional closure",
    "compile_other alone is not permanently hard.",
    "A repairable failure retains eligibility unless cumulative evidence lowers its relative priority.",
    "give exploration and justified recovery representation before filling remaining slots by priority; adapt this "
    "to prefix evidence rather than fixed quotas.",
    "Recovery must not displace normal successful refinements or leave workers idle.",
    "Do not stop while an eligible high-priority recovery or underexplored candidate remains; every remaining action "
    "needs an evidence-based decision to continue, reserve, or close.",
    "adapt batch composition after every revealed prefix.",
    "Beta has three distinct roles. Do not conflate them:",
    "Route recovery eligibility, reserve threshold, and waiting through the same schedule",
    "Scores alone do not establish that beta caused a change, so always use both sources:",
    "clamped to [0, 1]",
    "rather than pretending the replay ceiling is a live stopping signal.",
    "It also reveals whether the policy batches.",
    "Do not select the default simply as the smallest beta that reaches a frozen trace's known ceiling.",
    "must never inspect a current episode's outcomes.",
    "do not inherit the template stub and do not delegate grid choice to the runner's fallback.",
    "It creates branches 0..W-1 and attempts 0..R; R is the number of refinements allowed after each root.",
    "Do not read raw trace outcomes or a current cycle result inside plan_grid.",
    "repeated hard, unrecoverable failures or strongly redundant directions: reduce width and depth conservatively;",
    "state that evidence is insufficient.",
    "the direction provider assigns those new roots their directions, and solve still decides which legal "
    "roots/frontiers to open, refine, prune, or stop.",
    "Do not choose roots merely because their branch id is small.",
    "The runtime grid is the hard bound: controller thresholds may use less, but can never create branches or "
    "attempts beyond the effective plan.",
    "verify that the edited method.py contains an override of plan_grid",
    "trace_pool, if present, may be read only outside solve().",
    "Before finishing, verify trajectory-based ranking, the stated success semantics, non-automatic zero-valid "
    "closure, deterministic recovery competition, and portfolio-level stop.",
    "update_closed(closed, prefix, question)",
    "even when valid == False or n_valid/n_total are unavailable.",
]


def _norm(s: str) -> str:
    s = s.replace("`", "").replace("*", "").replace("–", "-").replace("’", "'")
    return re.sub(r"\s+", " ", s).strip().lower()


@pytest.mark.parametrize("objective", ["eq1", "pareto"])
def test_developer_prompt_carries_the_listing2_rules(objective):
    p = _norm(developer_prompt(objective, 4))
    assert not (missing := [r for r in L2_RULES if _norm(r) not in p]), missing
    if objective == "pareto":
        assert _norm("pareto.reward = pareto.auc - lambda * parallel_penalty") in p
        assert _norm("A serial policy has penalty near 1; useful full batches approach 1/W.") in p
    assert "framework notes (this reimplementation's runner; not part of the paper's prompt)" in p


def test_llm_developer_sends_the_listing2_prompt_and_all_history():
    llm = mock_developer_llm()
    res = run(SyntheticDomain(SyntheticConfig(seed=3)), config=_cfg(rounds=3, W=4, branch_count=4, refine_count=3, M=3),
              developer=LLMPolicyDeveloper(llm))
    assert res.usage["_cost"]["developer_calls"] == 4
    last = _norm(llm.calls[-1]["prompt"])
    assert all(_norm(r) in last for r in L2_RULES[:5])
    # every earlier version is in the context, each with its own execution traces
    assert last.count("/proposal_results/policy_execution_traces.jsonl ---") >= 4


def _ctx(siblings=(), history=(), **kw) -> AttemptContext:
    dom = SumDiffDomain()
    return AttemptContext(dom.describe(), "root", dom.seed_artifact(), 0.91, 0, 0, 2, [], list(siblings),
                          list(history), 0.91, {"direction": "hill"}, "Direction assigned to this branch: hill.",
                          ["construct.py"], **kw)


def _instructions(ctx: AttemptContext, **kw) -> str:
    return EditorAgent(MockLLM(lambda *a: ""), editable=["construct.py"], **kw).build_instructions(ctx)


def test_exploration_prompt_is_listing1():
    p = _instructions(_ctx())
    for phrase in ("Never execute pkill, kill, killall, or terminate unrelated processes.",
                   "not a sample, not just recent cycles or the current branch",
                   "Look at the shape of what's been tried.",
                   "(not just guessed from the proposal)",
                   "`$node_dir` is your own attempt directory -- exclude it when scanning sibling `attempt_*/` dirs.",
                   "Write only `$node_dir/proposal.md` (mechanism, evidence from history, why it's not a repeat, "
                   "expected benefit/risk) and `$node_dir/$eval_program`. Everything else is read-only.",
                   "For each, read its matching `eval/score.json` (and `error.txt` if it failed)."):
        assert phrase in p, phrase
    assert p.startswith("You must read every historical proposal")
    assert "Direction assigned to this branch: hill." in p and "$direction_guidance" not in p


def test_exploration_prompt_shows_every_record_in_full_or_says_what_it_hides():
    long = "mechanism " + "x" * 2000 + " END-OF-PROPOSAL"
    hist = [AttemptRecord(f"t1/b{i}.a0", i, 0, 1, f"# idea {i}\n\n{long}", 1.0 + i / 100) for i in range(50)]
    sib = [AttemptRecord("b1.a0", 1, 0, 1, "# sib", None, "compile_other", "Traceback\n  ...\nSyntaxError: x")]
    ctx = _ctx(siblings=sib, history=hist, baseline_proposal="the seed construction")
    full = _instructions(ctx)
    assert all(f"# idea {i}" in full for i in range(50)) and full.count("END-OF-PROPOSAL") == 50
    assert "omitted" not in full and "clipped" not in full
    assert "SyntaxError: x" in full and "the seed construction" in full  # error.txt and the baseline proposal.md
    capped = _instructions(ctx, max_history=10, max_proposal_chars=100)
    assert "(40 older attempts omitted by the calling system's history cap)" in capped
    assert "clipped by the calling system's history cap" in capped and "END-OF-PROPOSAL" not in capped
    assert "NOTE: this calling system caps what it shows" in capped


def test_loop_passes_every_earlier_search_to_the_agent():
    nodes = [DiscoveryNode(f"b{i}.a0", "root", i, 0, i + 1, 1.0) for i in range(30)]
    fake_worlds = [SimpleNamespace(meta={"round": r}, non_root=lambda: nodes) for r in range(1, 12)]
    assert len(history_records(fake_worlds)) == 330
    seen = []

    class Spy:
        def __init__(self, inner):
            self.inner = inner

        def attempt(self, ctx, *, seed):
            seen.append((ctx.round, len(ctx.history), ctx.history_note))
            return self.inner.attempt(ctx, seed=seed)

    dom = SyntheticDomain(SyntheticConfig(seed=5))
    cfg = _cfg(rounds=10, W=2, branch_count=1, refine_count=0, dream=False)
    run(dom, config=cfg, agent=Spy(dom.mock_agent()))
    assert seen[-1] == (10, 9, "")
    seen.clear()
    run(dom, config=dataclasses.replace(cfg, rounds=4, agent_history_cycles=2), agent=Spy(dom.mock_agent()))
    assert seen[-1][1] == 2 and "only the last 2 of the 3" in seen[-1][2]


def test_dream_rounds_never_exceed_the_fixed_per_round_budget():
    """A policy may plan a wider grid, but a round spends at most the fallback grid's 3 x 2 calls [paper §4 p.7]."""
    wide = REFINE.replace('return GridPlan(int(context.fallback_branch_count), int(context.fallback_refine_count),',
                          'return GridPlan(6, 3,')
    dom = SyntheticDomain(SyntheticConfig(seed=11))
    cfg = _cfg(rounds=2, W=3, branch_count=3, refine_count=1, dream=False)
    res = run(dom, config=cfg, initial_policy=wide)
    assert [r["plan"]["branch_count"] for r in res.trajectory] == [6, 6]
    assert all(r["calls"] <= 6 and r["round_budget"] == 6 for r in res.trajectory)
    assert res.meta["manifests"][0]["round_budget"] == 6
    free = run(dom, config=dataclasses.replace(cfg, round_budget=None), initial_policy=wide)
    assert free.trajectory[0]["calls"] == 24  # the plan's 6 x 4 cells


def test_live_solve_gets_the_round_budget():
    """Online, ``solve(question, budget)`` receives the round's call budget."""
    code = REFINE.replace("            legal = question.legal_actions()",
                          "            if budget is not None and len(question.observed()) >= budget // 2:\n"
                          "                break\n            legal = question.legal_actions()")
    dom = SyntheticDomain(SyntheticConfig(seed=2))
    cfg = _cfg(rounds=1, W=2, branch_count=2, refine_count=1, dream=False)
    assert run(dom, config=cfg, initial_policy=code).trajectory[0]["calls"] == 2  # budget 4: stop at 2
    assert run(dom, config=dataclasses.replace(cfg, round_budget=None), initial_policy=code).trajectory[0]["calls"] == 4


def test_e3_and_e6_run_every_workspace_in_parallel(dream_script):
    for settings in (dream_script("e3_dream_vs_fixed").SETTINGS, dream_script("e6_pacing").SET):
        for d, st in settings.items():
            assert st["W"] >= st["grid"][0], d


def test_cost_meter_counts_the_policy_developer():
    res = run(SyntheticDomain(SyntheticConfig(seed=4)), config=_cfg(rounds=3, W=4, branch_count=4, refine_count=3, M=4))
    cost = res.usage["_cost"]
    assert cost["developer_revisions"] == 2 * 3  # 2 dreaming phases x (M - 1) revisions
    assert cost["llm_calls_total"] == cost["agent_calls"] + 6
    meter = CostMeter()
    meter.add_agent(Usage(1, 10, 10, 0.3))
    meter.add_developer(Usage(2, 10, 10, 0.1))
    snap = meter.snapshot()
    assert snap["llm_calls_total"] == 3 and snap["developer_usd_share"] == pytest.approx(0.25)


def test_adaptive_plan_grid_does_not_call_one_manifest_improving():
    policy = _load_class(template_code("adaptive"))
    man = {"planned_grid": {"branch_count": 3, "refine_count": 4}, "final_best": 1.0, "gain_early": 0.5,
           "gain_late": 0.5, "max_depth": 4, "hard_fail_frac": 0.0}

    def plan(history: list[dict]) -> GridPlan:
        return policy({}).plan_grid(GridPlanningContext(
            history=history, fallback_branch_count=3, fallback_refine_count=4, hard_max_branch_count=12,
            hard_max_refine_count=12, worker_cap=3))

    one = plan([man])
    assert "improving" not in one.reason and "insufficient" in one.reason
    assert (one.branch_count, one.refine_count) == (3, 4)
    assert "still improving" in plan([dict(man, final_best=0.9), man]).reason


def _numeric_autoconv(h, m: int = 40) -> np.ndarray:
    """Brute-force f*f on a fine grid, f normalised to integral 1 on [-1/4, 1/4]."""
    x = np.repeat(np.asarray(h, float), m)
    dx = 0.5 / (len(h) * m)
    x = x / (x.sum() * dx)
    return np.convolve(x, x) * dx


def test_autocorrelation_objectives_are_exact():
    const = objectives([1.0] * 16)
    assert const["phi1"] == pytest.approx(2.0) and const["phi3"] == pytest.approx(2.0)
    assert const["phi2"] == pytest.approx(2.0 / 3.0)
    rng = np.random.default_rng(0)
    for signed in (False, True):
        h = rng.random(12) + (-0.3 if signed else 0.1)
        g, o = _numeric_autoconv(h), objectives(h)
        assert o["phi3"] == pytest.approx(np.max(np.abs(g)), rel=2e-3)
        if not signed:
            assert o["phi1"] == pytest.approx(np.max(g), rel=2e-3)
            dx = 0.5 / (12 * 40)
            assert o["phi2"] == pytest.approx((np.sum(g ** 2) * dx) / (np.sum(g) * dx * np.max(g)), rel=5e-3)


def test_autocorrelation_domain_checks_scores_and_improves():
    d3, d1 = AutocorrelationDomain("phi3", sandboxed=False), AutocorrelationDomain("phi1", sandboxed=False)
    assert d3.evaluate_program(d3.seed_artifact()).score == pytest.approx(-2.0)
    signed = [1.0, -0.2, 1.0, 1.0]
    unprojected = program("phi1", signed, None).replace("h = project(np.asarray(INIT, dtype=float))",
                                                        "h = np.asarray(INIT, dtype=float)")
    assert d1.evaluate_program(Artifact({"construct.py": unprojected})).fail_class == "constraint"  # phi1: f >= 0
    ev3 = d3.evaluate_program(Artifact({"construct.py": program("phi3", signed, None)}))
    assert ev3.fail_class == "ok" and ev3.score == pytest.approx(-objectives(signed)["phi3"])
    res = run(d3, config=_cfg(rounds=3, W=3, branch_count=3, refine_count=2, M=3, seed=1))
    assert res.meta["best_score"] > res.meta["seed_score"] + 0.2  # Phi3 from 2.0 to below 1.8
    assert -res.meta["best_score"] > 1.4557 - 1e-9  # never below the best known bound


def test_sandbox_fixes_the_string_hash_seed(worlds):
    """A policy iterating a set of cell ids picks the same batch order in every sandbox process."""
    code = REFINE.replace(
        "            order = sorted(legal, key=lambda c: (question.meta(c).attempt, question.meta(c).branch))",
        "            order = [c for c in set(legal)]            # set iteration: string-hash order")
    orders = set()
    for _ in range(3):
        ev = ReplayEvaluator(W=4, runner="subprocess", fallback=(4, 3), root_mode="addressable")
        orders.add(tuple(tuple(r["batch"]) for r in ev.evaluate(code, worlds[:1]).episodes[0].trace))
    assert len(orders) == 1
