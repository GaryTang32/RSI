"""Stream protocol and open-world guard: no labels, no grader, no future tasks, no replay."""
from __future__ import annotations

import pytest

from rsi.core.domain import Execution
from rsi.core.llm import MockLLM
from rsi.harness2 import (Config, GraderAccessError, GraderLeak, OpenWorld, StreamViolation, TaskStream, open_task,
                          run, stream_order)
from rsi.harness2 import context as C
from rsi.harness2.api import stream_tasks
from rsi.harness2.guard import ANSWER_KEYS, markers_for


def _stream_ids(dom, cfg):
    return [t.id for t in stream_tasks(dom, cfg)]


def test_rollouts_get_open_tasks_only(go, dom):
    go(k=1)
    rolls = [e for e in dom.events if e[0] == "rollout"]
    assert rolls and all(target is None for _, _, target, _, _ in rolls)
    assert all(not any(ANSWER_KEYS.search(k) for k in meta) for _, _, _, meta, _ in rolls)


def test_grader_is_used_only_after_the_loop(go, dom):
    res, _ = go(k=1)
    kinds = [e[0] for e in dom.events]
    last_roll = max(i for i, k in enumerate(kinds) if k == "rollout")
    graded = [i for i, k in enumerate(kinds) if k == "grade"]
    assert graded and min(graded) > last_roll                 # posthoc_report only
    assert res.meta["report"]["n"] == len(res.trajectory)
    _, _ = go(k=1, report=False, out="noreport")
    assert len([e for e in dom.events if e[0] == "grade"]) == len(graded)   # report=False never grades


def test_tasks_run_in_stream_order_without_replay(go, dom):
    res, _ = go(k=2)
    order = [e[1] for e in dom.events if e[0] == "rollout"]
    firsts = list(dict.fromkeys(order))
    assert firsts == [r["task"] for r in res.trajectory]
    # each task's rollouts are contiguous: once the next task starts, a task never runs again
    blocks = [t for i, t in enumerate(order) if i == 0 or order[i - 1] != t]
    assert blocks == firsts
    assert all(order.count(t) == 8 for t in firsts)           # R = 4k with k = 2


def test_improvers_never_see_future_tasks(go, dom, suite):
    res, imp = go(k=1)
    ids = [r["task"] for r in res.trajectory]
    by_task: dict[str, list[str]] = {}
    seq = iter(imp.prompts)
    for r in res.meta["records"]:
        n = sum(s.attempts for s in r.stages)
        by_task[r.task_id] = [next(seq)[1] for _ in range(n)]
    for i, tid in enumerate(ids):
        later = [suite.get(t).input for t in ids[i + 1:]]
        for prompt in by_task[tid]:
            assert suite.get(tid).input in prompt
            assert not any(q in prompt for q in later)
            assert C.parse(prompt)["context/task.md"] == suite.get(tid).input   # the request only, no target


def test_task_stream_refuses_replay_and_peeking(suite):
    tasks = suite.split("holdout", allow_sealed=True)
    s = TaskStream(tasks)
    it = iter(s)
    first = next(it)
    assert first.target is None and s.current_id == tasks[0].id
    s.assert_current(tasks[0].id)
    with pytest.raises(StreamViolation, match="has not arrived"):
        s.assert_current(tasks[1].id)
    next(it)
    with pytest.raises(StreamViolation, match="replay"):
        s.assert_current(tasks[0].id)
    assert list(it)[-1].id == tasks[-1].id and s.current_id is None


def test_open_task_strips_target_and_answer_meta(suite):
    t = suite.split("holdout", allow_sealed=True)[0]
    import dataclasses
    t2 = dataclasses.replace(t, meta={"entities": ["x"], "answer": 1, "expected_output": 2, "Rubric": 3})
    o = open_task(t2)
    assert o.target is None and o.meta == {"entities": ["x"]} and o.input == t.input
    assert open_task(t2, strip=False) is t2


def test_open_world_refuses_grading_and_restores(dom):
    t = dom.tasks.split("evolve")[0]
    with OpenWorld(dom):
        for name in ("grade", "run", "grade_execution"):
            with pytest.raises(GraderAccessError):
                getattr(dom, name)(t, Execution(output="x"))
        with pytest.raises(GraderAccessError):          # a BaseException: `except Exception` cannot swallow it
            try:
                dom.grade(t, Execution(output="x"))
            except Exception:  # noqa: BLE001
                pass
    assert dom.grade(t, Execution(output=f"ANSWER: {t.target}"))[0] == 1.0


def test_grader_verdict_in_evidence_is_a_leak(dom, suite):
    """A trajectory carrying a grader phrase must never reach an improver: the run stops with GraderLeak."""
    leaky = MockLLM(lambda p, s, seed, i: "Correct (answer '7').\nANSWER: 7")
    with pytest.raises(GraderLeak):
        run(dom, dom.seed_artifact(), llm_task=leaky, llm_propose=None, config=Config(split="holdout", max_tasks=1))
    assert "Correct (answer " in markers_for(dom) and '"pass_rate"' in markers_for(dom)


def test_proposed_tree_with_a_verdict_is_a_leak(dom, sim):
    class Leaky(MockLLM):
        pass
    bad = Leaky(lambda p, s, seed, i: "<notes>x</notes>\n=== FILE: prompts/system.md ===\n{\"pass_rate\": 1}\n")
    with pytest.raises(GraderLeak):
        run(dom, dom.seed_artifact(), llm_task=sim, llm_propose=bad, config=Config(split="holdout", max_tasks=1))


def test_stream_order_modes(dom):
    sorted_ids = _stream_ids(dom, Config(split="holdout+ood"))
    fams = [dom.tasks.get(t).family for t in sorted_ids]
    assert fams == sorted(fams)                                # grouped by domain, sorted
    a = _stream_ids(dom, Config(split="holdout+ood", order="shuffle", seed=1))
    b = _stream_ids(dom, Config(split="holdout+ood", order="shuffle", seed=1))
    c = _stream_ids(dom, Config(split="holdout+ood", order="shuffle", seed=2))
    assert a == b and sorted(a) == sorted(sorted_ids) and (a != sorted_ids or c != sorted_ids)
    listed = [t.id for t in dom.tasks.split("holdout", allow_sealed=True)]
    assert _stream_ids(dom, Config(split="holdout", order="split")) == listed
    assert _stream_ids(dom, Config()) == sorted(listed)        # default: the sealed holdout split
    assert len(_stream_ids(dom, Config(split="holdout+ood", max_tasks=3))) == 3
    with pytest.raises(ValueError, match="unknown split"):
        _stream_ids(dom, Config(split="nope"))
    assert [t.id for t in stream_order(dom, dom.tasks.split("evolve"), "split", 0)] == dom.tasks.splits["evolve"]
