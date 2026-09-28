"""Second adversarial review of Dream-RSI: regressions for the bugs it found, and a second
genericity test on a brand-new problem.

Bugs covered:
* a policy could peek through its own memory: probe everything, ``question.reset()`` (which
  wiped the episode's counters), then walk straight to the remembered best cell - it passed
  the static check and the guard, in both runners;
* a disqualified episode scored a constant -1, which beats an honest policy on a paper-scale
  world (32 x 20 grid) or with raw negative scores;
* ``DomainTask`` silently scored every program 0 as a *successful* evaluation on a domain
  without an ``evolve`` split;
* the Lasso grader trusted the candidate's own process: patching ``time.process_time`` or
  memoising across timing repeats multiplied the score by 10^3-10^5;
* with ``objective="pareto"`` the guarded selector ranked by Eq.1 per-world values, and the
  developer was shown a beta sweep computed on held-out worlds;
* ``default_beta_of`` ignored the ``self.config.get("beta", x)`` idiom of LLM-written policies;
  the default-beta rule could not lower a default sitting at the top of the grid;
* in ``root_mode="earliest"`` revealed root cells lost their tags (online they keep them);
* ``run()`` did not meter LLMs inside an explicitly passed agent / developer;
* ``run(..., task_kwargs=...)`` silently ignored the kwargs for discovery domains.
"""
import json
import math

import pytest

from rsi.core import Artifact, FunctionDomain, MockLLM, SealedSplitError, Task, TaskSuite, transfer_report
from rsi.domains.discovery import LassoPathDomain, SyntheticConfig, SyntheticDomain
from rsi.dream import (Config, DomainTask, DreamRSILoop, EditorAgent, EpisodeResult, Eq1Objective, GuardViolation,
                       LLMPolicyDeveloper, ParametricAgent, ParametricMutator, PrefixGuard, QuestionProxy,
                       ReplayEvaluator, ReplayQuestion, choose_default_beta, code_of, mock_developer_llm,
                       parallel_refine, run, template_code)
from rsi.dream.developer import default_beta_of
from rsi.dream.guard import InProcessRunner, SubprocessRunner


def _world(seed=5, grid=(4, 4)):
    res = run(SyntheticDomain(SyntheticConfig(seed=seed)),
              config=Config(rounds=1, W=4, branch_count=grid[0], refine_count=grid[1], dream=False,
                            sandbox="inprocess"))
    return res.meta["worlds"][0]


# ------------------------------------------------------------------------ guard: reset
@pytest.mark.parametrize("runner", [InProcessRunner(), SubprocessRunner(timeout_s=20)])
def test_reset_after_probing_is_a_guard_violation(runner):
    w = _world()
    code = template_code("peek_reset")
    assert static_ok(code)                                   # uses only the public API
    honest = ReplayEvaluator(W=4, runner=runner, root_mode="addressable").evaluate(code_of(parallel_refine()), [w])
    cheat_off = ReplayEvaluator(W=4, runner="inprocess", root_mode="addressable", unguarded=True).evaluate(code, [w])
    assert cheat_off.value > honest.value                    # without the rule the peek pays off
    rep = ReplayEvaluator(W=4, runner=runner, root_mode="addressable").evaluate(code, [w])
    e = rep.episodes[0]
    assert e.disqualified and any("reset() after probing" in v for v in e.violations)
    assert rep.value < honest.value


def static_ok(code):
    from rsi.dream import static_check

    return static_check(code).ok


def test_reset_before_the_first_probe_is_allowed():
    q = ReplayQuestion(_world(), 4, root_mode="addressable")
    g = PrefixGuard(q)
    p = QuestionProxy(g.transport)
    p.reset()
    p.reset()                                                # harmless: nothing revealed yet
    p.probe_batch(p.legal_roots()[:2])
    with pytest.raises(GuardViolation):
        p.reset()
    assert g.violations and q.N == 2                         # the reveal was not wiped


# ------------------------------------------------------------- disqualified episodes
def test_disqualified_scores_below_every_honest_episode():
    obj = Eq1Objective()                                     # normalized, beta1 = 0.01
    full = EpisodeResult("w", None, {}, {}, False, N=640, k=20, batch_sizes=[32] * 20, best=1.0, root=0.0,
                         ceiling=1.0, world_size=640, W=32)   # paper Flash grid, refine everything
    bad = EpisodeResult("w", None, {}, {}, False, N=0, k=0, batch_sizes=[], best=None, root=0.0, ceiling=1.0,
                        world_size=640, W=32, error="policy crashed")
    assert obj.score_episode(full) == pytest.approx(1.0 - 6.4 + 0.005 * 32)
    assert obj.score_episode(bad) < obj.score_episode(full)  # was -1.0 > -5.24
    raw = Eq1Objective(normalize=False)                      # raw negative scores (e.g. a negated loss)
    honest = EpisodeResult("w", None, {}, {}, False, N=3, k=1, batch_sizes=[3], best=-2.5, root=-3.0, ceiling=-2.5,
                           world_size=3, W=3)
    cheat = EpisodeResult("w", None, {}, {}, False, N=1, k=1, batch_sizes=[1], best=-2.5, root=-3.0, ceiling=-2.5,
                          world_size=3, W=3, violations=["tree"])
    assert raw.score_episode(cheat) < raw.score_episode(honest) < 0


# ------------------------------------------------------------------- DomainTask split
def _fdomain(splits):
    tasks = [Task(f"t{i}", i, i % 2) for i in range(6)]
    return FunctionDomain(TaskSuite(tasks, splits), lambda a, t, s, l: 1, lambda t, o: float(o == t.target))


def test_domain_task_uses_a_decision_split_and_never_fakes_success():
    dt = DomainTask(_fdomain({"train": ["t0", "t1", "t2"], "holdout": ["t3", "t4", "t5"]}), Artifact({"p": "x"}))
    assert dt.split == "train"
    ev = dt.evaluate(Artifact({"p": "x"}))
    assert ev.n_total == 3 and ev.score == pytest.approx(1 / 3)     # output 1 vs targets 0, 1, 0
    with pytest.raises(ValueError):                          # no decision split at all
        DomainTask(_fdomain({"holdout": ["t0", "t1"]}), Artifact({"p": "x"}))
    with pytest.raises(ValueError):                          # sealed split requested explicitly
        DomainTask(_fdomain({"evolve": ["t0"], "holdout": ["t1"]}), Artifact({"p": "x"}), split="holdout")
    with pytest.raises(ValueError):                          # empty split requested explicitly
        DomainTask(_fdomain({"evolve": ["t0"], "val": []}), Artifact({"p": "x"}), split="val")


def test_task_kwargs_are_not_silently_ignored_for_discovery_domains():
    with pytest.raises(TypeError):
        run(SyntheticDomain(), config=Config(rounds=1, sandbox="inprocess"), task_kwargs={"k": 3})


# ----------------------------------------------------------------- Lasso grader locking
def test_lasso_sandboxed_grader_cannot_be_influenced_by_the_candidate():
    dom = LassoPathDomain(sandboxed=True, repeats=2)
    seed = dom.seed_artifact()["solver.py"]
    base = dom.evaluate_program(Artifact({"solver.py": seed})).score
    patch_clock = "import time as _t\n_t.process_time = lambda: 0.0\n" + seed
    memo = seed.replace(
        "def lasso_path(X, y, lambdas):",
        "_M = {}\n\ndef lasso_path(X, y, lambdas):\n    k = np.asarray(X).tobytes()\n    if k not in _M:\n"
        "        _M[k] = _lp(X, y, lambdas)\n    return _M[k]\n\n\ndef _lp(X, y, lambdas):")
    forge = ("import atexit, json\natexit.register(lambda: print('\\n__LASSO__' + json.dumps("
             "{'times': [1e-9, 1e-9], 'objs': [[0.0] * 10, [0.0] * 10]})))\n" + seed)
    for code in (patch_clock, memo, forge):
        ev = dom.evaluate_program(Artifact({"solver.py": code}))
        assert ev.fail_class == "ok" and ev.score < 3.0 * base    # was 10^3 - 10^5 x
    wrong = "def lasso_path(X, y, lambdas):\n    return [[0.0] * 3 for _ in lambdas]\n"
    ev = dom.evaluate_program(Artifact({"solver.py": wrong}))
    assert ev.fail_class == "correctness" and ev.score == 0.0


# ------------------------------------------------------------ Pareto + guarded selector
class _SpyDeveloper(ParametricMutator):
    def __init__(self):
        super().__init__()
        self.seen = []

    def revise(self, ctx, *, seed=0):
        self.seen.append(ctx)
        return super().revise(ctx, seed=seed)


def test_pareto_objective_with_guarded_selector_uses_pareto_values_and_hides_holdout():
    dom = SyntheticDomain(SyntheticConfig(seed=3))
    spy = _SpyDeveloper()
    cfg = Config(rounds=4, W=3, branch_count=3, refine_count=2, M=2, objective="pareto", selector="guarded",
                 sandbox="inprocess", seed=3, dream_last=True)
    loop = DreamRSILoop(dom.as_task(), dom.mock_agent(), config=cfg, developer=spy)
    res = loop.run()
    obj = loop.replay.objective
    # per-world values are Pareto rewards of each world's own sweep
    rep = loop.replay.evaluate(res.meta["policy"], loop.worlds)
    for i, w in enumerate(loop.worlds):
        by_beta = {e.beta: [e] for e in rep.sweep_episodes if e.world_id == w.world_id}
        assert rep.per_world[i] == pytest.approx(obj.sweep(by_beta)["reward"])
    # the developer never sees held-out worlds, neither in episodes nor in the sweep
    last = spy.seen[-1]
    hold = {loop.worlds[i].world_id for i in loop.selector.holdout_worlds(len(loop.worlds))}
    assert hold
    dev_ids = {loop.worlds[i].world_id for i in loop.selector.dev_worlds(len(loop.worlds))}
    checked = 0
    for v in last.versions + last.history:
        if v.report is None:
            continue
        assert not hold & {e.world_id for e in v.report.episodes}
        if v.iteration == last.iteration and v.report.sweep is not None:
            # the value / sweep the developer sees = the Pareto sweep recomputed on the dev worlds only
            full = next(h.report for h in loop.history if h.index == v.index and h.iteration == v.iteration)
            by_beta = {}
            for e in full.sweep_episodes:
                if e.world_id in dev_ids:
                    by_beta.setdefault(e.beta, []).append(e)
            assert v.report.value == pytest.approx(obj.sweep(by_beta)["reward"])
            assert v.report.sweep == obj.sweep(by_beta)
            assert v.report.value != pytest.approx(full.value)   # the all-worlds value would leak the holdout
            checked += 1
    assert checked >= 1
    phases = [r["dream"] for r in res.trajectory if "dream" in r]
    assert phases and all(all(math.isfinite(x) for x in p["values"]) for p in phases)


# ----------------------------------------------------------------- default beta rules
LLM_STYLE = '''from policy_api import GridPlan, LLMDesignedMethod
NAME = "OptimalPolicy"
class OptimalPolicy(LLMDesignedMethod):
    def __init__(self, config=None):
        super().__init__(config)
        self.beta = float(self.config.get("beta", 0.8))
'''


def test_default_beta_of_reads_the_listing2_idiom():
    assert default_beta_of(LLM_STYLE) == pytest.approx(0.8)
    assert default_beta_of(code_of(parallel_refine())) == pytest.approx(0.6)
    assert default_beta_of("x = 1\n") == pytest.approx(0.6)


def test_default_beta_rule_lowers_a_default_at_the_top_of_the_grid():
    sweep = {"points": [{"beta": 0.8, "attainment": 0.9, "probes_frac": 0.5, "V_eq1": 0.5},
                        {"beta": 1.0, "attainment": 0.9, "probes_frac": 0.8, "V_eq1": 0.4}]}
    plateau = [{"final_best": 1.0}, {"final_best": 1.0}]
    b, why = choose_default_beta(1.0, plateau, sweep)
    assert b == pytest.approx(0.9) and "lower" in why         # was a jump to 0.6 ("conflicts")


# ---------------------------------------------------------------- earliest-mode tags
def test_earliest_mode_shows_tags_of_revealed_roots_only():
    w = _world(seed=6)
    q = ReplayQuestion(w, 4, root_mode="earliest")
    p = QuestionProxy(PrefixGuard(q).transport)
    p.reset()
    root = p.legal_roots()[-1]
    assert p.meta(root).tags == {}                           # a slot: its tags would describe the wrong branch
    got = p.probe_batch([root])[0]
    assert p.meta(got.cell_id).tags.get("direction") == w.branch_tags[got.branch]["direction"]


# ---------------------------------------------------------------- metering LLM roles
def _agent_llm():
    def respond(prompt, system, seed, i):
        cur = prompt.split("--- CURRENT ARTIFACT ---", 1)[1]
        c = json.loads(cur.split("=== FILE: w.json ===", 1)[1].strip().split("\n", 1)[0])
        c[(seed or 0) % 3] += 0.1
        return '```json\n{"change": "nudge"}\n```\n=== FILE: w.json ===\n' + json.dumps(c) + "\n"

    return MockLLM(respond, name="agent-llm")


# ------------------------------------------------------ genericity: a brand-new problem
XS_TRAIN = [i / 10 for i in range(0, 20, 2)]
XS_HOLD = [i / 10 for i in range(1, 20, 2)]


def _curve_domain(log):
    """NEW problem: fit the coefficients of 2x^2 - x + 0.5 (the artifact is a JSON program);
    only a ``train`` split for decisions, a sealed ``holdout``."""
    tasks = [Task(f"x{x:.1f}", x, 2 * x * x - x + 0.5, family="curve") for x in XS_TRAIN + XS_HOLD]
    splits = {"train": [f"x{x:.1f}" for x in XS_TRAIN], "holdout": [f"x{x:.1f}" for x in XS_HOLD]}

    def execute(art, task, seed, llm):
        log.append(task.id)
        a, b, c = json.loads(art["w.json"])
        return a * task.input ** 2 + b * task.input + c

    def grade(task, out):
        return 1.0 / (1.0 + abs(out - task.target))

    return FunctionDomain(TaskSuite(tasks, splits, name="curve"), execute, grade, name="curve",
                          description="w.json: [a, b, c] of a*x^2 + b*x + c; score = mean 1/(1+|error|)")


def _mutate(parent, rng, ctx):
    c = json.loads(parent["w.json"])
    k = int(ctx.direction["direction"][1:]) if ctx.direction.get("direction") else rng.randrange(3)
    c[k] = round(c[k] + rng.choice([-1, 1]) * rng.uniform(0.05, 0.6), 4)
    return Artifact({"w.json": json.dumps(c)}), f"# nudge c{k}"


def test_generic_train_split_domain_pareto_guarded_subprocess():
    log = []
    dom = _curve_domain(log)
    seed = Artifact({"w.json": "[0.0, 0.0, 0.0]"})
    cfg = Config(rounds=4, W=3, branch_count=3, refine_count=2, M=3, objective="pareto", selector="guarded",
                 sandbox="subprocess", seed=11)
    kw = dict(agent=ParametricAgent(_mutate), config=cfg, task_kwargs={"directions": ["c0", "c1", "c2"],
                                                                       "workers": 1})
    res = run(dom, seed, **kw)                               # a plain rsi.core Domain, no wrapper by the user
    assert res.meta["best_score"] > res.meta["seed_score"]
    assert all(t in {f"x{x:.1f}" for x in XS_TRAIN} for t in log)       # decisions only on `train`
    with pytest.raises(SealedSplitError):
        dom.tasks.split("holdout")
    rep = transfer_report(dom, None, {"seed": seed, "best": res.best}, splits=("train", "holdout"), workers=1)
    assert rep["splits"]["holdout"]["best"]["S"] > rep["splits"]["holdout"]["seed"]["S"]
    phases = [r["dream"] for r in res.trajectory if "dream" in r]
    assert len(phases) == 3 and all(len(p["values"]) == 3 for p in phases)
    res2 = run(_curve_domain([]), seed, **kw)                # deterministic offline, sandbox included
    assert res2.meta["best_score"] == res.meta["best_score"] and res2.meta["policy"] == res.meta["policy"]


def test_run_meters_llms_inside_explicit_agent_and_developer():
    dom = _curve_domain([])
    seed = Artifact({"w.json": "[0.0, 0.0, 0.0]"})
    agent = EditorAgent(_agent_llm(), editable=["w.json"])
    dev = LLMPolicyDeveloper(mock_developer_llm())
    res = run(dom, seed, agent=agent, developer=dev,
              config=Config(rounds=2, W=2, branch_count=2, refine_count=1, M=2, sandbox="inprocess"),
              task_kwargs={"workers": 1})
    assert res.usage["agent"]["calls"] == res.usage["_cost"]["agent_calls"] > 0
    assert res.usage["developer"]["calls"] == res.usage["_cost"]["developer_calls"] == 1
