"""Regressions for the claims-audit fixes (docs/claims/dream-rsi.md, "Fix log").

Each test fails on the code before the fix:

* N1 - no peeking through state kept ACROSS replay episodes (module-level memo, class attributes,
  the random state): fresh policy namespace per episode in both runners + the static check;
* N2 - the Listing-2 developer prompt carries the paper's rules verbatim;
* N5 - the Listing-1 exploration prompt carries the pkill line and the other dropped phrases, shows
  the baseline's proposal.md, and never claims a full history while hiding part of it;
* N3 - a Dream round never spends more agent calls than Fixed's per-round budget;
* N4 - E3 / E6 run every workspace of the fixed grid in parallel (W >= grid width);
* N7 - the cost meter counts the policy developer;
* the policy sandbox fixes the string-hash seed (a set-iterating policy is reproducible);
* the adaptive template does not call a single live manifest "still improving";
* the autocorrelation task (App. A Problem 4) exists and is exact.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

from rsi.core.llm import MockLLM
from rsi.domains.discovery import AutocorrelationDomain, SumDiffDomain, SyntheticConfig, SyntheticDomain
from rsi.domains.discovery.autocorr import objectives, program
from rsi.dream import (Config, EditorAgent, ParetoSweepObjective, ReplayEvaluator, run, static_check,
                       template_code)
from rsi.dream.agent import AttemptContext, AttemptRecord, history_records
from rsi.dream.cost import CostMeter
from rsi.dream.developer import developer_prompt
from rsi.dream.guard import SubprocessSession
from rsi.dream.policy_api import GridPlanningContext

ROOT = Path(__file__).resolve().parents[1]


def _worlds(n: int = 3):
    out = []
    for s in range(n):
        dom = SyntheticDomain(SyntheticConfig(seed=40 + s))
        cfg = Config(rounds=1, W=4, branch_count=4, refine_count=3, dream=False, sandbox="inprocess", seed=s,
                     agent_workers=1, trace=False)
        out.append(run(dom, config=cfg).meta["worlds"][0])
    return out


# ------------------------------------------------------------------------------------ N1
@pytest.mark.parametrize("runner", ["inprocess", "subprocess"])
def test_memo_cheater_gains_nothing_across_replay_episodes(runner):
    """The claims auditor's exploit (memo_module): remember each world's best cell in a module-level
    dict, walk straight to it on the next visit (the beta sweep revisits every world)."""
    worlds = _worlds()
    memo, pr = template_code("memo_module"), template_code("parallel_refine")
    ev = ReplayEvaluator(ParetoSweepObjective(), W=4, runner=runner, fallback=(4, 3))
    r_memo, r_pr = ev.evaluate(memo, worlds), ev.evaluate(pr, worlds)
    assert r_memo.disqualified == 0
    # fresh namespace per episode: _MEMO is always empty, the cheater IS parallel refine
    assert r_memo.value == pytest.approx(r_pr.value)
    assert [e.N for e in r_memo.sweep_episodes] == [e.N for e in r_pr.sweep_episodes]
    # the pre-fix behaviour (one namespace for all episodes) is what the attack exploited
    old = ReplayEvaluator(ParetoSweepObjective(), W=4, runner=runner, fallback=(4, 3), fresh_episodes=False)
    assert old.evaluate(memo, worlds).value > r_pr.value + 0.1


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
#: one probe per round; the number of rounds depends on module state + class state (both runners) ...
COUNTER_POLICY = STATE_POLICY.replace("__ROUNDS__", "len(_SEEN) + self.shared[\"n\"]")
#: ... or on a message written into the random module's state by an earlier episode (a channel only the
#: subprocess sandbox closes; the static check rejects random.seed / setstate)
RANDOM_POLICY = ("import random\nMAGIC = random.Random(12345).random()\n" + STATE_POLICY).replace(
    "__ROUNDS__", "5 if abs(random.random() - MAGIC) < 1e-12 else 2").replace(
    "        return finalize_result(question, res)", "        random.seed(12345)\n"
                                                    "        return finalize_result(question, res)")


@pytest.mark.parametrize("runner", ["inprocess", "subprocess"])
def test_every_replay_episode_starts_from_a_fresh_namespace(runner):
    world = _worlds(1)[0]
    ev = ReplayEvaluator(ParetoSweepObjective(), W=4, runner=runner, fallback=(4, 3))
    rep = ev.evaluate(COUNTER_POLICY, [world])          # the same world, 1 + 5 sweep episodes
    assert {e.N for e in [*rep.episodes, *rep.sweep_episodes]} == {2}
    old = ReplayEvaluator(ParetoSweepObjective(), W=4, runner=runner, fallback=(4, 3), fresh_episodes=False)
    rep_old = old.evaluate(COUNTER_POLICY, [world])
    assert [e.N for e in [*rep_old.episodes, *rep_old.sweep_episodes]] == [2, 4, 6, 8, 10, 12]


def test_sandbox_episodes_do_not_share_the_random_state():
    world = _worlds(1)[0]
    rep = ReplayEvaluator(ParetoSweepObjective(), W=4, runner="subprocess", fallback=(4, 3)).evaluate(
        RANDOM_POLICY, [world])
    assert {e.N for e in [*rep.episodes, *rep.sweep_episodes]} == {2}
    old = ReplayEvaluator(ParetoSweepObjective(), W=4, runner="subprocess", fallback=(4, 3), fresh_episodes=False)
    rep_old = old.evaluate(RANDOM_POLICY, [world])
    assert [e.N for e in [*rep_old.episodes, *rep_old.sweep_episodes]] == [2, 5, 5, 5, 5, 5]
    assert not static_check(RANDOM_POLICY).ok


def test_subprocess_session_forks_a_fresh_episode_and_survives_a_crash():
    code = COUNTER_POLICY.replace("        rounds = ", "        if self.config.get('crash'):\n"
                                  "            raise SystemExit(3)\n        rounds = ")
    world = _worlds(1)[0]
    from rsi.dream.question import ReplayQuestion
    from rsi.dream.policy_api import GridPlan

    with SubprocessSession(code, timeout_s=20) as sess:
        ns = []
        for crash in (False, True, False, False):
            assert sess.begin_episode() is None
            q = ReplayQuestion(world, 4, GridPlan(4, 3))
            out = sess.solve({"crash": crash}, q)
            ns.append(None if crash else q.N)
            assert (out.error is not None) == crash
        assert ns[0] == ns[2] == ns[3] and sess.episodes == 4


def test_static_check_rejects_state_that_outlives_an_episode():
    base = template_code("parallel_refine")
    bad = {
        "module dict store": base.replace('NAME = "OptimalPolicy"', 'NAME = "OptimalPolicy"\n_M = {}').replace(
            "        res = SimResult()", "        res = SimResult()\n        _M[1] = 2"),
        "module list append": base.replace('NAME = "OptimalPolicy"', 'NAME = "OptimalPolicy"\n_L = []').replace(
            "        res = SimResult()", "        res = SimResult()\n        _L.append(1)"),
        "global": base.replace('NAME = "OptimalPolicy"', 'NAME = "OptimalPolicy"\n_K = 0').replace(
            "        res = SimResult()", "        global _K\n        res = SimResult()\n        _K = 1"),
        "imported module attribute": base.replace("        res = SimResult()",
                                                  "        res = SimResult()\n        import math\n        math.memo = 1")
        .replace("        import math\n", ""),
        "class attribute via self": base.replace("    def plan_grid", "    cache = {}\n\n    def plan_grid").replace(
            "        res = SimResult()", "        res = SimResult()\n        self.cache[1] = 2"),
        "type(self)": base.replace("        res = SimResult()", "        res = SimResult()\n        type(self).x = 1"),
        "__class__": base.replace("        res = SimResult()", "        res = SimResult()\n        self.__class__.x = 1"),
        "lru_cache": base.replace("class OptimalPolicy", "import functools\n\n\n@functools.lru_cache(None)\n"
                                                         "def f(x):\n    return x\n\n\nclass OptimalPolicy"),
        "memo cheater": template_code("memo_module"),
    }
    for what, code in bad.items():
        assert not static_check(code).ok, what
    ok = {
        "templates": template_code("adaptive"),
        "local mutation + closure over an enclosing local": base.replace(
            "        res = SimResult()", "        res = SimResult()\n        seen = set()\n"
                                        "        mark = lambda c: seen.add(c)\n        mark(1)\n"
                                        "        def inner(x):\n            seen.add(x)\n        inner(2)"),
        "instance cache rebound per instance": base.replace(
            "    def plan_grid", "    cache = {}\n\n    def plan_grid").replace(
            "        res = SimResult()", "        res = SimResult()\n        self.cache = {}\n        self.cache[1] = 2"),
        "read-only module constant": base.replace('NAME = "OptimalPolicy"', 'NAME = "OptimalPolicy"\nP = {"a": 1}')
        .replace("        res = SimResult()", "        res = SimResult()\n        x = P.get('a')"),
    }
    for what, code in ok.items():
        assert static_check(code).ok, (what, static_check(code).errors)
    for name in ("parallel_refine", "adaptive", "rules", "peek_reset"):
        assert static_check(template_code(name)).ok, name


def test_recorded_llm_policies_still_pass_the_static_check():
    """No false positive on the recorded validation policies (LLM-written and mutator versions)."""
    paths = sorted(ROOT.glob("validation/dream-rsi/*/history/*/method.py"))
    assert paths
    bad = [str(p) for p in paths if not static_check(p.read_text()).ok]
    assert not bad, bad


# ------------------------------------------------------------------------------------ N2
#: Listing-2 rules the condensed prompt had dropped (claims audit N2), quoted from App. B.2
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
    import re

    s = s.replace("`", "").replace("*", "").replace("–", "-").replace("’", "'")
    return re.sub(r"\s+", " ", s).strip().lower()


@pytest.mark.parametrize("objective", ["eq1", "pareto"])
def test_developer_prompt_carries_the_listing2_rules(objective):
    p = _norm(developer_prompt(objective, 4))
    missing = [r for r in L2_RULES if _norm(r) not in p]
    assert not missing, missing
    if objective == "pareto":
        assert _norm("pareto.reward = pareto.auc - lambda * parallel_penalty") in p
        assert _norm("A serial policy has penalty near 1; useful full batches approach 1/W.") in p
    # this reimplementation's additions are kept apart from the paper's text
    assert "framework notes (this reimplementation's runner; not part of the paper's prompt)" in p


def test_llm_developer_sends_the_listing2_prompt_and_all_history():
    from rsi.dream import LLMPolicyDeveloper, mock_developer_llm

    llm = mock_developer_llm()
    res = run(SyntheticDomain(SyntheticConfig(seed=3)),
              config=Config(rounds=3, W=4, branch_count=4, refine_count=3, M=3, sandbox="inprocess", trace=False,
                            agent_workers=1), developer=LLMPolicyDeveloper(llm))
    assert res.usage["_cost"]["developer_calls"] == 4
    last = _norm(llm.calls[-1]["prompt"])
    for r in L2_RULES[:5]:
        assert _norm(r) in last
    # every earlier version is in the context, each with its own execution traces (no 6-version cap)
    assert last.count("/proposal_results/policy_execution_traces.jsonl ---") >= 4


# ------------------------------------------------------------------------------------ N5
def _ctx(lineage=(), siblings=(), history=(), **kw):
    dom = SumDiffDomain()
    return AttemptContext(dom.describe(), "root", dom.seed_artifact(), 0.91, 0, len(lineage), 2, list(lineage),
                          list(siblings), list(history), 0.91, {"direction": "hill"},
                          "Direction assigned to this branch: hill.", ["construct.py"], **kw)


def test_exploration_prompt_is_listing1():
    p = EditorAgent(MockLLM(lambda *a: ""), editable=["construct.py"]).build_instructions(_ctx())
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
    full = EditorAgent(MockLLM(lambda *a: ""), editable=["construct.py"]).build_instructions(ctx)
    assert all(f"# idea {i}" in full for i in range(50))                  # no 30-record cap
    assert full.count("END-OF-PROPOSAL") == 50                             # no 600-char clip
    assert "omitted" not in full and "clipped" not in full
    assert "SyntaxError: x" in full and "the seed construction" in full    # error.txt, baseline proposal.md
    capped = EditorAgent(MockLLM(lambda *a: ""), editable=["construct.py"], max_history=10,
                         max_proposal_chars=100).build_instructions(ctx)
    assert "(40 older attempts omitted by the calling system's history cap)" in capped
    assert "clipped by the calling system's history cap" in capped and "END-OF-PROPOSAL" not in capped
    assert "NOTE: this calling system caps what it shows" in capped


def test_loop_passes_every_earlier_search_to_the_agent():
    assert len(history_records([_FakeWorld(r, 30) for r in range(1, 12)])) == 330    # was capped at 200
    seen = []

    class Spy:
        def __init__(self, inner):
            self.inner = inner

        def attempt(self, ctx, *, seed):
            seen.append((ctx.round, len(ctx.history), ctx.history_note))
            return self.inner.attempt(ctx, seed=seed)

    dom = SyntheticDomain(SyntheticConfig(seed=5))
    run(dom, config=Config(rounds=10, W=2, branch_count=1, refine_count=0, dream=False, sandbox="inprocess",
                           trace=False, agent_workers=1), agent=Spy(dom.mock_agent()))
    assert seen[-1] == (10, 9, "")                                        # all 9 earlier searches (was 8)
    seen.clear()
    run(dom, config=Config(rounds=4, W=2, branch_count=1, refine_count=0, dream=False, sandbox="inprocess",
                           trace=False, agent_workers=1, agent_history_cycles=2), agent=Spy(dom.mock_agent()))
    assert seen[-1][1] == 2 and "only the last 2 of the 3" in seen[-1][2]


class _FakeWorld:
    def __init__(self, rnd, n):
        from rsi.dream.tree import DiscoveryNode

        self.meta = {"round": rnd}
        self._nodes = [DiscoveryNode(f"b{i}.a0", "root", i, 0, i + 1, 1.0) for i in range(n)]

    def non_root(self):
        return self._nodes


# ------------------------------------------------------------------------------------ N3
WIDE_POLICY = template_code("parallel_refine").replace(
    'return GridPlan(int(context.fallback_branch_count), int(context.fallback_refine_count),',
    'return GridPlan(6, 3,')


def test_dream_rounds_never_exceed_the_fixed_per_round_budget():
    """"Dream-RSI maintains identical per-round budgets" [paper:§4 p.7]: a policy may plan a wider /
    deeper grid, but a round spends at most the fallback grid's calls (3 x 2 = 6 here)."""
    dom = SyntheticDomain(SyntheticConfig(seed=11))
    cfg = Config(rounds=2, W=3, branch_count=3, refine_count=1, dream=False, sandbox="inprocess", trace=False,
                 agent_workers=1, hard_max_branch=12, hard_max_refine=12)
    res = run(dom, config=cfg, initial_policy=WIDE_POLICY)
    assert [r["plan"]["branch_count"] for r in res.trajectory] == [6, 6]          # the plan is kept ...
    assert all(r["calls"] <= 6 and r["round_budget"] == 6 for r in res.trajectory)   # ... the budget binds
    assert res.meta["manifests"][0]["round_budget"] == 6
    free = run(dom, config=Config(**{**cfg.__dict__, "round_budget": None}), initial_policy=WIDE_POLICY)
    assert free.trajectory[0]["calls"] == 24                                    # pre-fix: 6 x 4 cells


def test_live_solve_gets_the_round_budget():
    """Online, solve(question, budget) receives the round's call budget (replay passes None)."""
    code = template_code("parallel_refine").replace(
        "            legal = question.legal_actions()",
        "            if budget is not None and len(question.observed()) >= budget // 2:\n"
        "                break\n            legal = question.legal_actions()")
    dom = SyntheticDomain(SyntheticConfig(seed=2))
    cfg = Config(rounds=1, W=2, branch_count=2, refine_count=1, dream=False, sandbox="inprocess", trace=False,
                 agent_workers=1)
    assert run(dom, config=cfg, initial_policy=code).trajectory[0]["calls"] == 2          # budget 4 -> stop at 2
    free = Config(**{**cfg.__dict__, "round_budget": None})
    assert run(dom, config=free, initial_policy=code).trajectory[0]["calls"] == 4


# ------------------------------------------------------------------------------------ N4
def test_e3_and_e6_run_every_workspace_in_parallel():
    sys.path.insert(0, str(ROOT / "experiments" / "dream-rsi"))
    import e3_dream_vs_fixed as e3
    import e6_pacing as e6

    for d, st in e3.SETTINGS.items():
        assert st["W"] >= st["grid"][0], d
    for d, st in e6.SET.items():
        assert st["W"] >= st["grid"][0], d


# ------------------------------------------------------------------------------------ N7
def test_cost_meter_counts_the_policy_developer():
    dom = SyntheticDomain(SyntheticConfig(seed=4))
    res = run(dom, config=Config(rounds=3, W=4, branch_count=4, refine_count=3, M=4, sandbox="inprocess",
                                 trace=False, agent_workers=1))
    cost = res.usage["_cost"]
    assert cost["developer_revisions"] == 2 * 3                         # 2 dreaming phases x (M - 1) revisions
    assert cost["llm_calls_total"] == cost["agent_calls"] + 6
    m = CostMeter()
    from rsi.core.llm import Usage

    m.add_agent(Usage(1, 10, 10, 0.3))
    m.add_developer(Usage(2, 10, 10, 0.1))
    snap = m.snapshot()
    assert snap["llm_calls_total"] == 3 and snap["developer_usd_share"] == pytest.approx(0.25)


# ------------------------------------------------------------------------------- template label
def test_adaptive_plan_grid_does_not_call_one_manifest_improving():
    from rsi.dream.guard import _load_class

    cls = _load_class(template_code("adaptive"))
    man = {"planned_grid": {"branch_count": 3, "refine_count": 4}, "final_best": 1.0, "gain_early": 0.5,
           "gain_late": 0.5, "max_depth": 4, "hard_fail_frac": 0.0}
    ctx = GridPlanningContext(history=[man], fallback_branch_count=3, fallback_refine_count=4,
                              hard_max_branch_count=12, hard_max_refine_count=12, worker_cap=3)
    plan = cls({}).plan_grid(ctx)
    assert "improving" not in plan.reason and "insufficient" in plan.reason
    assert (plan.branch_count, plan.refine_count) == (3, 4)
    two = GridPlanningContext(history=[dict(man, final_best=0.9), man], fallback_branch_count=3,
                              fallback_refine_count=4, hard_max_branch_count=12, hard_max_refine_count=12,
                              worker_cap=3)
    assert "still improving" in cls({}).plan_grid(two).reason


# ------------------------------------------------------------------------------- autocorrelation
def _numeric_autoconv(h, m=40):
    """Brute-force (f*f) on a fine grid (f normalised to integral 1 on [-1/4, 1/4])."""
    n = len(h)
    x = np.repeat(np.asarray(h, float), m)
    dx = 0.5 / (n * m)
    x = x / (x.sum() * dx)
    return np.convolve(x, x) * dx


def test_autocorrelation_objectives_are_exact():
    const = objectives([1.0] * 16)
    assert const["phi1"] == pytest.approx(2.0) and const["phi3"] == pytest.approx(2.0)
    assert const["phi2"] == pytest.approx(2.0 / 3.0)
    rng = np.random.default_rng(0)
    for signed in (False, True):
        h = rng.random(12) + (-0.3 if signed else 0.1)
        g = _numeric_autoconv(h)
        o = objectives(h)
        assert o["phi3"] == pytest.approx(np.max(np.abs(g)), rel=2e-3)
        if not signed:
            assert o["phi1"] == pytest.approx(np.max(g), rel=2e-3)
            dx = 0.5 / (12 * 40)
            phi2 = (np.sum(g ** 2) * dx) / (np.sum(g) * dx * np.max(g))
            assert o["phi2"] == pytest.approx(phi2, rel=5e-3)


def test_autocorrelation_domain_checks_scores_and_improves():
    d3 = AutocorrelationDomain("phi3", sandboxed=False)
    d1 = AutocorrelationDomain("phi1", sandboxed=False)
    assert d3.evaluate_program(d3.seed_artifact()).score == pytest.approx(-2.0)
    signed = [1.0, -0.2, 1.0, 1.0]
    from rsi.core.artifact import Artifact

    art1 = Artifact({"construct.py": program("phi1", signed, None).replace(
        "h = project(np.asarray(INIT, dtype=float))", "h = np.asarray(INIT, dtype=float)")})
    assert d1.evaluate_program(art1).fail_class == "constraint"         # phi1 needs f >= 0
    art3 = Artifact({"construct.py": program("phi3", signed, None)})
    ev3 = d3.evaluate_program(art3)
    assert ev3.fail_class == "ok" and ev3.score == pytest.approx(-objectives(signed)["phi3"])
    res = run(d3, config=Config(rounds=3, W=3, branch_count=3, refine_count=2, M=3, sandbox="inprocess",
                                trace=False, agent_workers=1, seed=1))
    assert res.meta["best_score"] > res.meta["seed_score"] + 0.2        # Phi3 from 2.0 to below 1.8
    assert -res.meta["best_score"] > 1.4557 - 1e-9                       # never below the best known bound


# ------------------------------------------------------------------- sandbox determinism
SET_ORDER_POLICY = template_code("parallel_refine").replace(
    "            order = sorted(legal, key=lambda c: (question.meta(c).attempt, question.meta(c).branch))",
    "            order = [c for c in set(legal)]            # set iteration: string-hash order")


def test_sandbox_fixes_the_string_hash_seed():
    """A policy that iterates a set of cell ids (the live run's r0002 does) picks the same batch order in
    every sandbox process, so its replay is reproducible across evaluations."""
    world = _worlds(1)[0]
    orders = set()
    for _ in range(3):
        rep = ReplayEvaluator(W=4, runner="subprocess", fallback=(4, 3), root_mode="addressable").evaluate(
            SET_ORDER_POLICY, [world])
        orders.add(tuple(tuple(r["batch"]) for r in rep.episodes[0].trace))
    assert len(orders) == 1
