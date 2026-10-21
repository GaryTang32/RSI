"""Adversarial-review tests for rsi.gepa: genericity on a brand-new inline domain, API edge cases, review fixes."""
import random
import warnings

import pytest

from rsi.core import LLM, Artifact, Execution, FunctionDomain, LLMResponse, Task, TaskSuite, Usage
from rsi.gepa import (Config, FewShotConfig, GenericReflectionLM, ParetoSelector, RLConfig, optimize, run,
                      run_best_of_n, run_fewshot, run_scalar_rl, run_score_only)

# A unit-conversion assistant: units.txt must state "1 <unit> = <k> <base>"; the grader names the missing fact.
FACTS = {"inch": ("cm", 2.54), "foot": ("cm", 30.48), "mile": ("km", 1.609), "pound": ("kg", 0.4536),
         "ounce": ("g", 28.35), "gallon": ("l", 3.785), "yard": ("m", 0.9144), "acre": ("m2", 4047.0)}
SEED = Artifact({"units.txt": "Convert the quantity to the base unit.\n", "executor.py": "# frozen\n"})


def _tasks(n: int, seed: int, prefix: str) -> list[Task]:
    rng = random.Random(seed)
    out = []
    for i in range(n):
        u = rng.choice(sorted(FACTS))
        q = rng.randint(1, 9)
        base, k = FACTS[u]
        out.append(Task(f"{prefix}{i}", {"qty": q, "unit": u}, round(q * k, 3), family=base))
    return out


def _execute(artifact, task, seed, llm):
    text, u = artifact["units.txt"], task.input["unit"]
    for line in text.splitlines():
        if f"1 {u} = " in line:
            k = float(line.split(f"1 {u} = ")[1].split()[0])
            return Execution(output=round(task.input["qty"] * k, 3), trace=f"used fact: {line}",
                             meta={"objectives": {"correct": 1.0, "brevity": 1.0 / (1 + len(text.splitlines()))}})
    return Execution(output=None, trace=f"no fact for {u}", meta={"objectives": {"correct": 0.0, "brevity": 1.0}})


def _grade(task, output):
    if output == task.target:
        return 1.0, "Correct."
    base, k = FACTS[task.input["unit"]]
    return 0.0, f"1 {task.input['unit']} = {k} {base}. Missing conversion fact."


def unit_domain(train_name: str = "evolve", with_val: bool = True) -> FunctionDomain:
    tr, va, te = _tasks(12, 0, "tr"), _tasks(10, 1, "va"), _tasks(20, 2, "te")
    splits = {train_name: [t.id for t in tr], "test": [t.id for t in te]}
    if with_val:
        splits["val"] = [t.id for t in va]
    suite = TaskSuite(tr + (va if with_val else []) + te, splits, name="units")
    return FunctionDomain(suite, _execute, _grade, name="units", description="unit converter prompt")


def _run(d, B: int, llm=None, *, selector=None, out_dir=None, report_splits=(), **cfg):
    return run(d, SEED, llm_propose=llm or GenericReflectionLM(), config=Config(max_metric_calls=B, **cfg),
               selector=selector, out_dir=out_dir, report_splits=report_splits)


def test_generic_function_domain_improves_and_keeps_frozen_files():
    res = _run(unit_domain(), 400, GenericReflectionLM(max_new=2), report_splits=("test",))
    assert res.meta["components"] == ["units.txt"]                       # *.py is frozen by default
    assert res.best["executor.py"] == SEED["executor.py"]
    assert res.meta["best_val"] > res.meta["seed_val"]
    rep = res.meta["report"]["splits"]["test"]
    assert rep["best"]["S"] > rep["seed"]["S"] + 0.5                    # lessons generalise to the sealed split
    assert res.usage["reflection"]["calls"] == res.meta["n_reflection_calls"] > 0
    lin = res.ledger.lineage(f"c{res.state.best_idx()}")
    assert lin[0].id == "c0" and lin[-1].artifact_id == res.best.id


def test_generic_domain_without_val_split_and_train_named_train():
    d = unit_domain(train_name="train", with_val=False)
    with pytest.warns(UserWarning, match="D_pareto = D_train"):
        res = _run(d, 200)
    assert res.meta["splits"] == {"train": "train", "val": "train"}
    assert res.meta["n_val"] == res.meta["n_train"] == 12
    with warnings.catch_warnings():
        warnings.simplefilter("error")                                   # explicit multi-task mode: no warning
        _run(d, 100, val_split=None)


@pytest.mark.parametrize("kw", [dict(candidate_selection="current_best"), dict(candidate_selection="beam_search"),
                                dict(candidate_selection="epsilon_greedy"), dict(candidate_selection="top_k_pareto"),
                                dict(frontier_type="hybrid"), dict(frontier_type="objective"),
                                dict(frontier_type="cartesian"), dict(use_merge=True),
                                dict(acceptance="noise_margin", noise_margin=0.05), dict(budget_mode="hard"),
                                dict(cache_evaluation=True, use_merge=True), dict(feedback="score_only")])
def test_every_option_runs_on_a_new_domain(kw):
    res = _run(unit_domain(), 250, **kw)
    assert res.meta["rollouts"] >= 250 or res.stop_reason.startswith("max_metric_calls")
    if kw.get("budget_mode") == "hard":
        assert res.meta["rollouts"] <= 250
    assert res.best in res.state.candidates


def test_objective_frontier_uses_trial_meta_objectives():
    res = _run(unit_domain(), 200, frontier_type="hybrid")
    keys = set(res.state.frontier.mapping())
    assert {"o:correct", "o:brevity"} <= keys and any(k.startswith("i:") for k in keys)


def test_non_unit_score_range_sets_perfect_score():
    class Scaled(FunctionDomain):
        score_range = (0.0, 10.0)

    def grade(task, output):
        score, feedback = _grade(task, output)
        return 10 * score, feedback

    res = _run(Scaled(unit_domain().tasks, _execute, grade, name="scaled"), 300, GenericReflectionLM(max_new=2))
    assert res.meta["perfect_score"] == 10.0
    assert res.meta["best_val"] > res.meta["seed_val"]


def test_baselines_run_on_a_new_domain():
    d, llm = unit_domain(), GenericReflectionLM()
    so = run_score_only(d, SEED, llm_propose=llm, config=Config(max_metric_calls=150))
    assert so.method == "score_only_reflection"
    bon = run_best_of_n(d, SEED, llm_propose=llm, config=Config(max_metric_calls=150))
    assert bon.meta["rollouts"] <= 150 and bon.meta["n_rewrites"] >= 1
    rl = run_scalar_rl(d, SEED, llm_propose=llm, config=RLConfig(max_metric_calls=300, val_every=2, vocab_size=4))
    assert rl.meta["rollouts"] >= 300 and "units.txt" in rl.meta["vocab_sizes"]
    fs = run_fewshot(d, SEED, llm_propose=llm, config=FewShotConfig(max_metric_calls=200, minibatch_size=5))
    assert fs.meta["rollouts"] <= 200


def test_seed_failing_everywhere_warns_and_is_counted():
    def broken(artifact, task, seed, llm):
        if llm is None:
            raise ValueError("this system needs a task LLM")

    d = FunctionDomain(unit_domain().tasks, broken, _grade, name="broken")
    with pytest.warns(RuntimeWarning, match="execution error on all 10 D_pareto examples"):
        res = _run(d, 60)
    assert res.meta["seed_val_error_rate"] == 1.0 and res.meta["n_exec_errors"] >= 10


def test_all_reflection_calls_failing_warns():
    class Down(LLM):
        name = "down"

        def _complete(self, prompt, *, system, max_tokens, seed):
            return LLMResponse(text="", usage=Usage(1), model="down", error="backend unavailable")

    with pytest.warns(RuntimeWarning, match="reflection-LM calls failed"):
        res = _run(unit_domain(), 60, Down())
    assert res.meta["n_reflection_failed"] == res.meta["n_reflection_calls"] > 0
    assert res.best == SEED and res.meta["n_proposals"] == 0


class NewestBest:
    name = "newest_best"

    def select(self, state):
        agg = state.agg_scores()
        return max(k for k, v in enumerate(agg) if v == max(agg))


def test_custom_selector_instance_and_factory_and_resume(tmp_path):
    d = unit_domain()
    res = _run(d, 200, selector=NewestBest())
    assert res.meta["selector"] == "newest_best"
    seen = []

    def factory(rng):
        sel = ParetoSelector(rng)
        seen.append(sel.rng)
        return sel

    full = _run(d, 200, selector=factory, out_dir=tmp_path / "a")
    assert res.meta["n_candidates"] >= 2 and seen and full.meta["selector"] == "pareto"
    # a selector without get_state/set_state is stateless, so it still resumes
    _run(d, 100, selector=NewestBest(), out_dir=tmp_path / "b")
    again = _run(d, 200, selector=NewestBest(), out_dir=tmp_path / "b")
    assert again.meta["resumed_at"] is not None and again.meta["rollouts"] >= 200


def test_optimize_without_valset_is_explicit_multitask_mode():
    def metric(cand, ex):
        return float(ex in cand["p.md"]), f"mention {ex}"

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        res = optimize({"p.md": "start"}, ["alpha", "beta", "gamma"], metric=metric,
                       llm_propose=GenericReflectionLM(max_new=3),
                       config=Config(max_metric_calls=60, train_split="train"))
    assert res.meta["splits"] == {"train": "evolve", "val": "evolve"} and res.meta["best_val"] == 1.0
