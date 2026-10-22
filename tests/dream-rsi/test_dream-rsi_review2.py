"""Second-review regressions and a genericity test on a new problem with only a ``train`` split."""
import json
import math

import pytest

from rsi.core import Artifact, FunctionDomain, MockLLM, SealedSplitError, Task, TaskSuite, transfer_report
from rsi.domains.discovery import LassoPathDomain, SyntheticConfig, SyntheticDomain
from rsi.dream import (Config, DomainTask, DreamRSILoop, EditorAgent, EpisodeResult, Eq1Objective, GuardViolation,
                       LLMPolicyDeveloper, ParametricAgent, ParametricMutator, PrefixGuard, QuestionProxy,
                       ReplayEvaluator, ReplayQuestion, choose_default_beta, code_of, mock_developer_llm,
                       parallel_refine, run, static_check, template_code)
from rsi.dream.developer import default_beta_of
from rsi.dream.guard import InProcessRunner, SubprocessRunner

LLM_STYLE = '''from policy_api import GridPlan, LLMDesignedMethod
NAME = "OptimalPolicy"
class OptimalPolicy(LLMDesignedMethod):
    def __init__(self, config=None):
        super().__init__(config)
        self.beta = float(self.config.get("beta", 0.8))
'''
XS_TRAIN = [i / 10 for i in range(0, 20, 2)]
XS_HOLD = [i / 10 for i in range(1, 20, 2)]
CURVE_SEED = Artifact({"w.json": "[0.0, 0.0, 0.0]"})


@pytest.mark.parametrize("runner", [InProcessRunner(), SubprocessRunner(timeout_s=20)])
def test_reset_after_probing_is_a_guard_violation(runner, synthetic_world):
    """``peek_reset`` probes everything, resets and walks to the remembered best cell, using only the public API."""
    w = synthetic_world(5)
    code = template_code("peek_reset")
    assert static_check(code).ok
    honest = ReplayEvaluator(W=4, runner=runner, root_mode="addressable").evaluate(code_of(parallel_refine()), [w])
    cheat_off = ReplayEvaluator(W=4, runner="inprocess", root_mode="addressable", unguarded=True).evaluate(code, [w])
    assert cheat_off.value > honest.value  # without the rule the peek pays off
    rep = ReplayEvaluator(W=4, runner=runner, root_mode="addressable").evaluate(code, [w])
    e = rep.episodes[0]
    assert e.disqualified and any("reset() after probing" in v for v in e.violations)
    assert rep.value < honest.value


def test_reset_before_the_first_probe_is_allowed(synthetic_world):
    q = ReplayQuestion(synthetic_world(5), 4, root_mode="addressable")
    g = PrefixGuard(q)
    p = QuestionProxy(g.transport)
    p.reset()
    p.reset()  # nothing revealed yet
    p.probe_batch(p.legal_roots()[:2])
    with pytest.raises(GuardViolation):
        p.reset()
    assert g.violations and q.N == 2  # the reveal was not wiped


def test_disqualified_scores_below_every_honest_episode():
    obj = Eq1Objective()  # normalized, beta1 = 0.01
    full = EpisodeResult("w", None, {}, {}, False, N=640, k=20, batch_sizes=[32] * 20, best=1.0, root=0.0,
                         ceiling=1.0, world_size=640, W=32)  # the paper's Flash grid, every cell refined
    bad = EpisodeResult("w", None, {}, {}, False, N=0, k=0, batch_sizes=[], best=None, root=0.0, ceiling=1.0,
                        world_size=640, W=32, error="policy crashed")
    assert obj.score_episode(full) == pytest.approx(1.0 - 6.4 + 0.005 * 32)
    assert obj.score_episode(bad) < obj.score_episode(full)
    raw = Eq1Objective(normalize=False)  # raw negative scores, e.g. a negated loss
    honest = EpisodeResult("w", None, {}, {}, False, N=3, k=1, batch_sizes=[3], best=-2.5, root=-3.0, ceiling=-2.5,
                           world_size=3, W=3)
    cheat = EpisodeResult("w", None, {}, {}, False, N=1, k=1, batch_sizes=[1], best=-2.5, root=-3.0, ceiling=-2.5,
                          world_size=3, W=3, violations=["tree"])
    assert raw.score_episode(cheat) < raw.score_episode(honest) < 0


def _fdomain(splits: dict) -> FunctionDomain:
    tasks = [Task(f"t{i}", i, i % 2) for i in range(6)]
    return FunctionDomain(TaskSuite(tasks, splits), lambda *a: 1, lambda t, o: float(o == t.target))


def test_domain_task_uses_a_decision_split_and_never_fakes_success():
    prog = Artifact({"p": "x"})
    dt = DomainTask(_fdomain({"train": ["t0", "t1", "t2"], "holdout": ["t3", "t4", "t5"]}), prog)
    assert dt.split == "train"
    ev = dt.evaluate(prog)
    assert ev.n_total == 3 and ev.score == pytest.approx(1 / 3)  # output 1 vs targets 0, 1, 0
    for splits, kw in (({"holdout": ["t0", "t1"]}, {}),  # no decision split at all
                       ({"evolve": ["t0"], "holdout": ["t1"]}, {"split": "holdout"}),  # sealed
                       ({"evolve": ["t0"], "val": []}, {"split": "val"})):  # empty
        with pytest.raises(ValueError):
            DomainTask(_fdomain(splits), prog, **kw)


def test_task_kwargs_are_not_silently_ignored_for_discovery_domains():
    with pytest.raises(TypeError):
        run(SyntheticDomain(), config=Config(rounds=1, sandbox="inprocess"), task_kwargs={"k": 3})


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
        assert ev.fail_class == "ok" and ev.score < 3.0 * base
    wrong = "def lasso_path(X, y, lambdas):\n    return [[0.0] * 3 for _ in lambdas]\n"
    ev = dom.evaluate_program(Artifact({"solver.py": wrong}))
    assert ev.fail_class == "correctness" and ev.score == 0.0


class _SpyDeveloper(ParametricMutator):
    def __init__(self):
        super().__init__()
        self.seen = []

    def revise(self, ctx, *, seed=0):
        self.seen.append(ctx)
        return super().revise(ctx, seed=seed)


def test_pareto_objective_with_guarded_selector_uses_pareto_values_and_hides_holdout():
    spy = _SpyDeveloper()
    cfg = Config(rounds=4, W=3, branch_count=3, refine_count=2, M=2, objective="pareto", selector="guarded",
                 sandbox="inprocess", seed=3, dream_last=True)
    dom = SyntheticDomain(SyntheticConfig(seed=3))
    loop = DreamRSILoop(dom.as_task(), dom.mock_agent(), config=cfg, developer=spy)
    res = loop.run()
    obj, worlds = loop.replay.objective, loop.worlds
    rep = loop.replay.evaluate(res.meta["policy"], worlds)
    for i, w in enumerate(worlds):  # per-world values are the Pareto rewards of each world's own sweep
        assert rep.per_world[i] == pytest.approx(obj.sweep({e.beta: [e] for e in rep.sweep_episodes
                                                             if e.world_id == w.world_id})["reward"])
    last = spy.seen[-1]
    hold = {worlds[i].world_id for i in loop.selector.holdout_worlds(len(worlds))}
    dev_ids = {worlds[i].world_id for i in loop.selector.dev_worlds(len(worlds))}
    assert hold
    checked = 0
    for v in last.versions + last.history:
        if v.report is None:
            continue
        assert not hold & {e.world_id for e in v.report.episodes}  # the developer never sees held-out worlds
        if v.iteration == last.iteration and v.report.sweep is not None:
            full = next(h.report for h in loop.history if h.index == v.index and h.iteration == v.iteration)
            by_beta = {}
            for e in full.sweep_episodes:
                if e.world_id in dev_ids:
                    by_beta.setdefault(e.beta, []).append(e)
            assert v.report.value == pytest.approx(obj.sweep(by_beta)["reward"])  # recomputed on dev worlds only
            assert v.report.sweep == obj.sweep(by_beta)
            assert v.report.value != pytest.approx(full.value)
            checked += 1
    assert checked >= 1
    phases = [r["dream"] for r in res.trajectory if "dream" in r]
    assert phases and all(all(math.isfinite(x) for x in p["values"]) for p in phases)


def test_default_beta_of_reads_the_listing2_idiom():
    assert default_beta_of(LLM_STYLE) == pytest.approx(0.8)
    assert default_beta_of(code_of(parallel_refine())) == pytest.approx(0.6)
    assert default_beta_of("x = 1\n") == pytest.approx(0.6)


def test_default_beta_rule_lowers_a_default_at_the_top_of_the_grid():
    sweep = {"points": [{"beta": 0.8, "attainment": 0.9, "probes_frac": 0.5, "V_eq1": 0.5},
                        {"beta": 1.0, "attainment": 0.9, "probes_frac": 0.8, "V_eq1": 0.4}]}
    b, why = choose_default_beta(1.0, [{"final_best": 1.0}, {"final_best": 1.0}], sweep)
    assert b == pytest.approx(0.9) and "lower" in why


def test_earliest_mode_shows_tags_of_revealed_roots_only(synthetic_world):
    w = synthetic_world(6)
    p = QuestionProxy(PrefixGuard(ReplayQuestion(w, 4, root_mode="earliest")).transport)
    p.reset()
    root = p.legal_roots()[-1]
    assert p.meta(root).tags == {}  # a slot: its tags would describe the wrong branch
    got = p.probe_batch([root])[0]
    assert p.meta(got.cell_id).tags.get("direction") == w.branch_tags[got.branch]["direction"]


def _curve_domain(log: list) -> FunctionDomain:
    """A new problem: fit the coefficients of 2x^2 - x + 0.5 (a JSON artifact); decisions on ``train`` only."""
    tasks = [Task(f"x{x:.1f}", x, 2 * x * x - x + 0.5, family="curve") for x in XS_TRAIN + XS_HOLD]
    splits = {"train": [f"x{x:.1f}" for x in XS_TRAIN], "holdout": [f"x{x:.1f}" for x in XS_HOLD]}

    def execute(art, task, seed, llm):
        log.append(task.id)
        a, b, c = json.loads(art["w.json"])
        return a * task.input ** 2 + b * task.input + c

    return FunctionDomain(TaskSuite(tasks, splits, name="curve"), execute,
                          lambda task, out: 1.0 / (1.0 + abs(out - task.target)), name="curve",
                          description="w.json: [a, b, c] of a*x^2 + b*x + c; score = mean 1/(1+|error|)")


def _mutate(parent, rng, ctx):
    c = json.loads(parent["w.json"])
    k = int(ctx.direction["direction"][1:]) if ctx.direction.get("direction") else rng.randrange(3)
    c[k] = round(c[k] + rng.choice([-1, 1]) * rng.uniform(0.05, 0.6), 4)
    return Artifact({"w.json": json.dumps(c)}), f"# nudge c{k}"


def test_generic_train_split_domain_pareto_guarded_subprocess():
    log = []
    dom = _curve_domain(log)
    cfg = Config(rounds=4, W=3, branch_count=3, refine_count=2, M=3, objective="pareto", selector="guarded",
                 sandbox="subprocess", seed=11)
    kw = {"agent": ParametricAgent(_mutate), "config": cfg,
          "task_kwargs": {"directions": ["c0", "c1", "c2"], "workers": 1}}
    res = run(dom, CURVE_SEED, **kw)  # a plain rsi.core Domain
    assert res.meta["best_score"] > res.meta["seed_score"]
    assert all(t in {f"x{x:.1f}" for x in XS_TRAIN} for t in log)
    with pytest.raises(SealedSplitError):
        dom.tasks.split("holdout")
    rep = transfer_report(dom, None, {"seed": CURVE_SEED, "best": res.best}, splits=("train", "holdout"), workers=1)
    assert rep["splits"]["holdout"]["best"]["S"] > rep["splits"]["holdout"]["seed"]["S"]
    phases = [r["dream"] for r in res.trajectory if "dream" in r]
    assert len(phases) == 3 and all(len(p["values"]) == 3 for p in phases)
    res2 = run(_curve_domain([]), CURVE_SEED, **kw)  # deterministic offline, sandbox included
    assert res2.meta["best_score"] == res.meta["best_score"] and res2.meta["policy"] == res.meta["policy"]


def test_run_meters_llms_inside_explicit_agent_and_developer():
    def respond(prompt, system, seed, i):
        cur = prompt.split("--- CURRENT ARTIFACT ---", 1)[1]
        c = json.loads(cur.split("=== FILE: w.json ===", 1)[1].strip().split("\n", 1)[0])
        c[(seed or 0) % 3] += 0.1
        return '```json\n{"change": "nudge"}\n```\n=== FILE: w.json ===\n' + json.dumps(c) + "\n"

    res = run(_curve_domain([]), CURVE_SEED, agent=EditorAgent(MockLLM(respond, name="agent-llm"), editable=["w.json"]),
              developer=LLMPolicyDeveloper(mock_developer_llm()),
              config=Config(rounds=2, W=2, branch_count=2, refine_count=1, M=2, sandbox="inprocess"),
              task_kwargs={"workers": 1})
    assert res.usage["agent"]["calls"] == res.usage["_cost"]["agent_calls"] > 0
    assert res.usage["developer"]["calls"] == res.usage["_cost"]["developer_calls"] == 1
