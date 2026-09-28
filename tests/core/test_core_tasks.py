"""rsi.core.tasks: Task, TaskSuite splits, sealing discipline, from_tasks, persistence."""
from __future__ import annotations

import pytest

from rsi.core import SealedSplitError, Task, TaskSuite
from rsi.core.tasks import DECISION_SPLITS, SEALED_SPLITS


def _tasks(n=10, fams=("a", "b")):
    return [Task(f"t{i}", {"x": i}, i * 2, fams[i % len(fams)], {"entities": [f"E{i}"]}) for i in range(n)]


def _suite():
    ts = _tasks(10)
    return TaskSuite(ts, {"evolve": ["t0", "t1", "t2"], "val": ["t3"], "holdout": ["t4", "t5"],
                          "ood": ["t6", "t7"], "test": ["t8"], "smoke": ["t0"]}, name="s")


def test_task_json_roundtrip_and_hash_ignores_meta():
    t = Task("id1", {"q": [1, 2]}, "ans", "fam", {"entities": ["Bob"]})
    assert Task.from_json(t.to_json()) == t
    assert Task.from_json({"id": "x", "input": 1}).family == "default"
    assert hash(Task("i", 1, 2, "f", {"a": 1})) == hash(Task("i", 1, 2, "f", {"b": 2}))
    with pytest.raises(Exception):
        t.id = "other"          # frozen


def test_split_roles_constants():
    assert set(DECISION_SPLITS) == {"evolve", "val", "train"}
    assert set(SEALED_SPLITS) == {"holdout", "ood", "test"}


def test_unknown_task_ids_rejected():
    with pytest.raises(KeyError):
        TaskSuite(_tasks(2), {"evolve": ["t0", "nope"]})


def test_sealed_splits_raise_until_unsealed():
    s = _suite()
    assert [t.id for t in s.split("evolve")] == ["t0", "t1", "t2"]
    assert [t.id for t in s.split("val")] == ["t3"]
    for name in ("holdout", "ood", "test"):
        assert s.is_sealed(name)
        with pytest.raises(SealedSplitError):
            s.split(name)
        assert s.split(name, allow_sealed=True)       # explicit report-only read
    assert not s.is_sealed("evolve") and not s.is_sealed("smoke")
    s.unseal("holdout")
    assert [t.id for t in s.split("holdout")] == ["t4", "t5"]
    with pytest.raises(SealedSplitError):
        s.split("ood")
    s.seal("holdout", "evolve", "not-a-split")
    with pytest.raises(SealedSplitError):
        s.split("evolve")
    assert not s.is_sealed("not-a-split")
    s.unseal()                                        # no names: unseal everything
    assert s.split("ood") and s.split("test") and s.split("evolve")


def test_sealed_error_is_runtime_error_and_missing_split_is_empty():
    assert issubclass(SealedSplitError, RuntimeError)
    assert _suite().split("nonexistent") == []


def test_accessors_families_summary():
    s = _suite()
    assert len(s) == 10 and s.get("t3").target == 6
    assert s.families() == ["a", "b"]
    assert s.families("val") == ["b"]
    summ = s.summary()
    assert summ["evolve"] == {"n": 3, "families": ["a", "b"]}
    assert summ["test"]["n"] == 1


def test_jsonl_roundtrip_reseals(tmp_path):
    s = _suite()
    s.unseal()
    p = tmp_path / "sub" / "suite.jsonl"
    s.to_jsonl(p)
    s2 = TaskSuite.from_jsonl(p)
    assert s2.name == "s" and s2.splits == s.splits
    assert s2.tasks == s.tasks and s2.get("t1").meta == {"entities": ["E1"]}
    with pytest.raises(SealedSplitError):              # sealing is a property of the split role
        s2.split("holdout")


def test_from_tasks_fractions_partition_and_determinism():
    ts = _tasks(101)
    s = TaskSuite.from_tasks(ts, fractions={"evolve": 0.5, "holdout": 0.3, "test": 0.2}, seed=3)
    ids = [i for v in s.splits.values() for i in v]
    assert sorted(ids) == sorted(t.id for t in ts)    # every task in exactly one split
    assert len(s.splits["evolve"]) == 50 and len(s.splits["holdout"]) == 30
    assert len(s.splits["test"]) == 21                 # remainder goes to the last split
    s_same = TaskSuite.from_tasks(ts, fractions={"evolve": 0.5, "holdout": 0.3, "test": 0.2}, seed=3)
    assert s_same.splits == s.splits
    s_other = TaskSuite.from_tasks(ts, fractions={"evolve": 0.5, "holdout": 0.3, "test": 0.2}, seed=4)
    assert s_other.splits != s.splits
    assert s.is_sealed("holdout") and s.is_sealed("test") and not s.is_sealed("evolve")


def test_from_tasks_unnormalized_fractions_and_tiny_pools():
    s = TaskSuite.from_tasks(_tasks(10), fractions={"evolve": 2, "holdout": 2})
    assert len(s.splits["evolve"]) == 5 and len(s.splits["holdout"]) == 5
    s = TaskSuite.from_tasks(_tasks(3), fractions={"a": 0.5, "b": 0.5, "c": 0.0})
    assert sorted(i for v in s.splits.values() for i in v) == ["t0", "t1", "t2"]
    with pytest.raises(ValueError):
        TaskSuite.from_tasks(_tasks(3), fractions={"a": 0.0})


def test_from_tasks_by_family():
    ts = _tasks(12, fams=("num", "dates", "strings"))
    s = TaskSuite.from_tasks(ts, fractions={"evolve": 0.5, "holdout": 0.5}, by_family={"ood": ["dates", "strings"]})
    assert s.families("ood") == ["dates", "strings"] and len(s.splits["ood"]) == 8
    assert s.families("evolve") == ["num"] and s.families("holdout") == ["num"]
    assert len(s.splits["evolve"]) + len(s.splits["holdout"]) == 4
