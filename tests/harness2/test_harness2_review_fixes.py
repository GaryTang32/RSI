"""Regression tests for two review findings: same-round leakage in sequential mode (M4) and the guard's
false positive on a task's own text (M5)."""

from __future__ import annotations

import pytest
from harness2_helpers import RecordingDomain, RecordingImprover

from rsi.core.llm import MockLLM
from rsi.harness2 import Config, GraderLeak, run
from rsi.harness2 import context as C

# ---------------------------------------------------------------- M4: sequential editors share one snapshot


@pytest.mark.parametrize("k", [1, 2, 3])
def test_sequential_dom_editor_does_not_see_inv_notes_of_the_same_round(go, k):
    _, imp = go(k=k, mode="sequential", max_tasks=1)
    editors = [p for role, p in imp.prompts if role == "editor"]
    assert len(editors) == 2 * k  # no invalid-proposal retries with the default improver
    for t in range(1, k + 1):
        inv, dom = (C.parse(p) for p in editors[2 * (t - 1) : 2 * t])
        assert f"context/decisions/inv{t}.md" not in dom  # never another candidate of the same round
        assert not any(key.startswith(f"context/pool/inv{t}/") for key in dom)
        dec = sorted(key for key in dom if key.startswith("context/decisions/"))
        assert dec == sorted(key for key in inv if key.startswith("context/decisions/"))  # same snapshot
        assert sorted(key for key in dom if key.startswith("context/pool/")) == sorted(
            key for key in inv if key.startswith("context/pool/")
        )
        if t == 1:  # pool/decisions only in rounds > 1
            assert not dec and not any(key.startswith("context/pool/") for key in dom)
        else:
            assert f"context/decisions/c{t - 1}.md" in dom


def test_parallel_editors_still_see_no_decisions(go):
    _, imp = go(k=2, max_tasks=1)
    for role, p in imp.prompts:
        if role == "editor":
            assert not any(key.startswith(("context/decisions/", "context/pool/")) for key in C.parse(p))


# ---------------------------------------------------------------- M5: the task's own text is not a grader leak

DATA = 'Data: {"score": 4, "review": "ok"}'


class ScoredInputDomain(RecordingDomain):
    """A task whose own request and source files carry a ``"score":`` key (user data, not a grader verdict)."""

    def task_prompt(self, task):
        return f"{task.input}\n\n{DATA}"

    def task_source(self, task):
        return {"reviews.json": '[{"score": 4, "review": "ok"}, {"score": 2, "review": "meh"}]'}


@pytest.fixture
def scored(suite):
    return ScoredInputDomain(suite)


@pytest.mark.parametrize("mode", ["parallel", "sequential"])
@pytest.mark.parametrize("k", [1, 2])
def test_task_text_with_a_score_key_is_not_a_leak(scored, sim, tmp_path, mode, k):
    imp = RecordingImprover()
    res = run(
        scored,
        scored.seed_artifact(),
        llm_task=sim,
        llm_propose=imp,
        config=Config(split="holdout", max_tasks=1, k=k, mode=mode),
        out_dir=tmp_path / "run",
    )
    rec = res.meta["records"][0]
    assert rec.status in ("ok", "partial") and rec.adopted is not None
    assert imp.prompts and all(DATA in p for _, p in imp.prompts)  # the improvers still get the task's data
    assert all("context/source/reviews.json" in C.parse(p) for role, p in imp.prompts if role != "reflector")


def test_task_input_with_a_score_key_echoed_in_the_trace_is_not_a_leak(suite, sim, tmp_path):
    """The raw task input reaches the rollout trace verbatim (the harness quotes it); that is not a leak."""
    import dataclasses

    dom = RecordingDomain(suite)
    task = dataclasses.replace(suite.split("holdout", allow_sealed=True)[0])
    task = dataclasses.replace(task, input=f"{task.input}\n{DATA}")
    res = run(
        dom,
        dom.seed_artifact(),
        llm_task=sim,
        llm_propose=RecordingImprover(),
        config=Config(k=1),
        tasks=[task],
        out_dir=tmp_path / "run",
    )
    rec = res.meta["records"][0]
    assert DATA in rec.rollouts["base1"].execution.trace
    assert rec.adopted is not None


def test_a_grader_score_in_the_trace_is_still_a_leak_when_the_task_has_one(scored, tmp_path):
    """Exempting the task's own text must not exempt the same key written by anything else."""
    leaky = MockLLM(lambda p, s, seed, i: 'grader: {"score": 0.0}\nANSWER: 7')
    with pytest.raises(GraderLeak, match='"score"'):
        run(
            scored,
            scored.seed_artifact(),
            llm_task=leaky,
            llm_propose=RecordingImprover(),
            config=Config(split="holdout", max_tasks=1),
        )
