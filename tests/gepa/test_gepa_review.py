"""Adversarial-review tests for rsi.gepa: genericity on brand-new domains (built inline with
rsi.core.FunctionDomain / a Domain subclass), API edge cases, and fixes found in review."""
import random
import warnings

import pytest

from rsi.core import Artifact, Execution, FunctionDomain, Task, TaskSuite
from rsi.gepa import (Config, FewShotConfig, GenericReflectionLM, RLConfig, run, run_best_of_n, run_fewshot,
                      run_scalar_rl, run_score_only)

# --------------------------------------------------------------------------- a new domain --
# A unit-conversion "assistant": the evolvable file ``units.txt`` must state conversion facts
# ("1 <unit> = <k> <base>"); the frozen executor answers a question by looking the fact up in
# the prompt. The grader says which fact is missing (text feedback, GEPA's mu_f).
FACTS = {"inch": ("cm", 2.54), "foot": ("cm", 30.48), "mile": ("km", 1.609), "pound": ("kg", 0.4536),
         "ounce": ("g", 28.35), "gallon": ("l", 3.785), "yard": ("m", 0.9144), "acre": ("m2", 4047.0)}


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
    text = artifact["units.txt"]
    u = task.input["unit"]
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


SEED = Artifact({"units.txt": "Convert the quantity to the base unit.\n", "executor.py": "# frozen\n"})


def test_generic_function_domain_improves_and_keeps_frozen_files():
    d = unit_domain()
    res = run(d, SEED, llm_propose=GenericReflectionLM(max_new=2), config=Config(max_metric_calls=400),
              report_splits=("test",))
    assert res.meta["components"] == ["units.txt"]                       # *.py is frozen by default
    assert res.best["executor.py"] == SEED["executor.py"]
    assert res.meta["best_val"] > res.meta["seed_val"]
    rep = res.meta["report"]["splits"]["test"]
    assert rep["best"]["S"] > rep["seed"]["S"] + 0.5                   # lessons generalise to the sealed split
    assert res.usage["reflection"]["calls"] == res.meta["n_reflection_calls"] > 0
    lin = res.ledger.lineage(f"c{res.state.best_idx()}")
    assert lin[0].id == "c0" and lin[-1].artifact_id == res.best.id


def test_generic_domain_without_val_split_and_train_named_train():
    d = unit_domain(train_name="train", with_val=False)
    with pytest.warns(UserWarning, match="D_pareto = D_train"):
        res = run(d, SEED, llm_propose=GenericReflectionLM(), config=Config(max_metric_calls=200))
    assert res.meta["splits"] == {"train": "train", "val": "train"}
    assert res.meta["n_val"] == res.meta["n_train"] == 12
    with warnings.catch_warnings():
        warnings.simplefilter("error")                                   # explicit multi-task mode: no warning
        run(d, SEED, llm_propose=GenericReflectionLM(), config=Config(max_metric_calls=100, val_split=None))


@pytest.mark.parametrize("kw", [dict(candidate_selection="current_best"), dict(candidate_selection="beam_search"),
                                dict(candidate_selection="epsilon_greedy"), dict(candidate_selection="top_k_pareto"),
                                dict(frontier_type="hybrid"), dict(frontier_type="objective"),
                                dict(frontier_type="cartesian"), dict(use_merge=True),
                                dict(acceptance="noise_margin", noise_margin=0.05), dict(budget_mode="hard"),
                                dict(cache_evaluation=True, use_merge=True), dict(feedback="score_only")])
def test_every_option_runs_on_a_new_domain(kw):
    d = unit_domain()
    res = run(d, SEED, llm_propose=GenericReflectionLM(), config=Config(max_metric_calls=250, **kw))
    assert res.meta["rollouts"] >= 250 or res.stop_reason.startswith("max_metric_calls")
    if kw.get("budget_mode") == "hard":
        assert res.meta["rollouts"] <= 250
    assert res.best in res.state.candidates


def test_objective_frontier_uses_trial_meta_objectives():
    d = unit_domain()
    res = run(d, SEED, llm_propose=GenericReflectionLM(), config=Config(max_metric_calls=200, frontier_type="hybrid"))
    keys = set(res.state.frontier.mapping())
    assert {"o:correct", "o:brevity"} <= keys and any(k.startswith("i:") for k in keys)


def test_non_unit_score_range_sets_perfect_score():
    class Scaled(FunctionDomain):
        score_range = (0.0, 10.0)

    base = unit_domain()
    d = Scaled(base.tasks, _execute, lambda t, o: tuple([10 * _grade(t, o)[0], _grade(t, o)[1]]), name="scaled")
    res = run(d, SEED, llm_propose=GenericReflectionLM(max_new=2), config=Config(max_metric_calls=300))
    assert res.meta["perfect_score"] == 10.0
    assert res.meta["best_val"] > res.meta["seed_val"]


def test_baselines_run_on_a_new_domain():
    d = unit_domain()
    llm = GenericReflectionLM()
    so = run_score_only(d, SEED, llm_propose=llm, config=Config(max_metric_calls=150))
    assert so.method == "score_only_reflection"
    bon = run_best_of_n(d, SEED, llm_propose=llm, config=Config(max_metric_calls=150))
    assert bon.meta["rollouts"] <= 150 and bon.meta["n_rewrites"] >= 1
    rl = run_scalar_rl(d, SEED, llm_propose=llm, config=RLConfig(max_metric_calls=300, val_every=2, vocab_size=4))
    assert rl.meta["rollouts"] >= 300 and "units.txt" in rl.meta["vocab_sizes"]
    fs = run_fewshot(d, SEED, llm_propose=llm, config=FewShotConfig(max_metric_calls=200, minibatch_size=5))
    assert fs.meta["rollouts"] <= 200


# ------------------------------------------------------------------ failures are never silent --
def test_seed_failing_everywhere_warns_and_is_counted():
    def broken(artifact, task, seed, llm):
        if llm is None:
            raise ValueError("this system needs a task LLM")

    d = FunctionDomain(unit_domain().tasks, broken, _grade, name="broken")
    with pytest.warns(RuntimeWarning, match="execution error on all 10 D_pareto examples"):
        res = run(d, SEED, llm_propose=GenericReflectionLM(), config=Config(max_metric_calls=60))
    assert res.meta["seed_val_error_rate"] == 1.0 and res.meta["n_exec_errors"] >= 10


def test_all_reflection_calls_failing_warns():
    from rsi.core import LLM, LLMResponse, Usage

    class Down(LLM):
        name = "down"

        def _complete(self, prompt, *, system, max_tokens, seed):
            return LLMResponse(text="", usage=Usage(1), model="down", error="backend unavailable")

    with pytest.warns(RuntimeWarning, match="reflection-LM calls failed"):
        res = run(unit_domain(), SEED, llm_propose=Down(), config=Config(max_metric_calls=60))
    assert res.meta["n_reflection_failed"] == res.meta["n_reflection_calls"] > 0
    assert res.best == SEED and res.meta["n_proposals"] == 0


# ------------------------------------------------------------------------- custom selector --
class NewestBest:
    name = "newest_best"

    def select(self, state):
        agg = state.agg_scores()
        return max(k for k, v in enumerate(agg) if v == max(agg))


def test_custom_selector_instance_and_factory_and_resume(tmp_path):
    d = unit_domain()
    res = run(d, SEED, llm_propose=GenericReflectionLM(), config=Config(max_metric_calls=200), selector=NewestBest())
    assert res.meta["selector"] == "newest_best"
    seen = []

    def factory(rng):
        from rsi.gepa import ParetoSelector
        sel = ParetoSelector(rng)
        seen.append(sel.rng)
        return sel
    full = run(d, SEED, llm_propose=GenericReflectionLM(), config=Config(max_metric_calls=200), selector=factory,
               out_dir=tmp_path / "a")
    assert res.meta["n_candidates"] >= 2 and seen and full.meta["selector"] == "pareto"
    # a selector without get_state/set_state still resumes (it is stateless)
    run(d, SEED, llm_propose=GenericReflectionLM(), config=Config(max_metric_calls=100), selector=NewestBest(),
        out_dir=tmp_path / "b")
    again = run(d, SEED, llm_propose=GenericReflectionLM(), config=Config(max_metric_calls=200),
                selector=NewestBest(), out_dir=tmp_path / "b")
    assert again.meta["resumed_at"] is not None and again.meta["rollouts"] >= 200


def test_optimize_without_valset_is_explicit_multitask_mode():
    def metric(cand, ex):
        return float(ex in cand["p.md"]), f"mention {ex}"

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        from rsi.gepa import optimize
        res = optimize({"p.md": "start"}, ["alpha", "beta", "gamma"], metric=metric,
                       llm_propose=GenericReflectionLM(max_new=3), config=Config(max_metric_calls=60,
                                                                                   train_split="train"))
    assert res.meta["splits"] == {"train": "evolve", "val": "evolve"} and res.meta["best_val"] == 1.0
