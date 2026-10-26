"""Harnessing-level recursion: the per-domain library is updated after each task and reused by later ones."""
from __future__ import annotations

from harness2_helpers import RecordingImprover

from rsi.core.artifact import Artifact
from rsi.harness2 import Library, MockReflector
from rsi.harness2 import context as C


def _by_task(res, imp):
    """Improver prompts grouped by the task they were made for."""
    seq, out = iter(imp.prompts), {}
    for r in res.meta["records"]:
        out[r.task_id] = [next(seq) for _ in range(sum(s.attempts for s in r.stages))]
    return out


def test_library_is_updated_after_each_task_and_read_by_later_tasks_of_its_domain(go, dom):
    res, imp = go(k=1)
    by_task = _by_task(res, imp)
    seen: dict[str, list[str]] = {}
    for row, rec in zip(res.trajectory, res.meta["records"]):
        earlier = seen.setdefault(rec.domain_key, [])
        for role, prompt in by_task[rec.task_id]:
            ctx = C.parse(prompt)
            lib = {k.split("/")[3] for k in ctx if k.startswith("context/library/harnesses/")}
            if role in ("editor", "composer"):
                assert lib == set(earlier)                   # exactly the earlier entries of THIS domain
                assert ("context/library/INDEX.md" in ctx) == bool(earlier)
            else:                                            # selector and reflector get no library
                assert not lib and "context/library/INDEX.md" not in ctx
        if rec.library_added:
            earlier.append(rec.task_id)
        assert row["library_added"] == bool(rec.library_added)
    assert res.meta["library_entries"] == sum(len(v) for v in seen.values()) > 0
    numeric = [r for r in res.meta["records"] if r.domain_key == "numeric"]
    assert len(numeric) == 4 and all(r.library_added for r in numeric)


def test_library_entries_hold_the_whole_adopted_tree_and_experience(go):
    res, _ = go(k=1)
    lib = Library(res.meta["library_dir"])
    rec = next(r for r in res.meta["records"] if r.library_added)
    entry = lib.dir(rec.domain_key) / "harnesses" / rec.task_id
    assert Artifact.from_dir(entry / "harness") == rec.adopted_tree
    md = (entry / "EXPERIENCE.md").read_text()
    for part in (f"# {rec.task_id}", f"adopted: `{rec.adopted}`", "## What each edit did", "**cue**", "**lesson**",
                 "**evidence**"):
        assert part in md
    index = (lib.dir(rec.domain_key) / "INDEX.md").read_text()
    assert f"harnesses/{rec.task_id}/harness/" in index and "cue:" in index


def test_reuse_later_dom_editor_copies_from_the_library(go):
    res, imp = go(k=1)
    numeric = [r for r in res.meta["records"] if r.domain_key == "numeric"]
    later = numeric[1]
    dom_stage = next(s for s in later.stages if s.label == "dom1")
    assert "copied" in dom_stage.notes and numeric[0].task_id in dom_stage.notes


def test_no_entry_stores_nothing_not_even_the_harness(go):
    res, imp = go(k=1, improver=RecordingImprover(reflector=MockReflector(force="none")))
    assert res.meta["library_entries"] == 0
    assert all(r.reflection.get("no_entry") and r.library_added is None for r in res.meta["records"])
    assert not any(k.startswith("context/library/") for _, p in imp.prompts for k in C.parse(p))


def test_library_off_is_the_ablation(go):
    res, imp = go(k=1, library=False)
    assert res.meta["library_entries"] == 0
    assert all(r.counts()["reflector"] == 0 for r in res.meta["records"])
    assert "reflector" not in [role for role, _ in imp.prompts]


def test_library_starts_empty_and_modes_are_separate(go, tmp_path):
    root = tmp_path / "lib"
    stale = root / "agg" / "numeric" / "harnesses" / "old" / "EXPERIENCE.md"
    stale.parent.mkdir(parents=True)
    stale.write_text("# old\n- **cue**: x\n")
    res, imp = go(k=1, library_dir=str(root), max_tasks=2)
    assert not stale.exists()                                # emptied at the start of the stream
    assert not any("harnesses/old" in p for _, p in imp.prompts)
    res2, _ = go(k=1, library_dir=str(root), max_tasks=2, mode="sequential", out="seq")
    assert res.meta["library_dir"].endswith("agg") and res2.meta["library_dir"].endswith("seq")
    assert (root / "agg").exists() and (root / "seq").exists()
