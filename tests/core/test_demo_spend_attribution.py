"""spend.py: recorded cache entries keep their attempt on any checkout; an unrecorded entry goes to the attempt
whose cache-write journal lists it, whatever the file times, and is otherwise reported instead of guessed."""
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "experiments" / "demo"))

import spend  # noqa: E402

T0 = 1_700_000_000.0
RECORDED = {".cache_run": {"run": ["aaaaaaaaaaaaaaaa"], "run/rescore": ["bbbbbbbbbbbbbbbb"]}}


def _entry(cache: Path, name: str, usd: float, mtime: float) -> str:
    key = name * 64
    p = cache / key[:2] / f"{key}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"usage": {"cost_usd": usd}}))
    os.utime(p, (mtime, mtime))
    return key


def _journal(path: Path, *keys: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f".cache_run/{k}\n" for k in keys))


def _checkout(cache: Path) -> None:
    for p in cache.rglob("*.json"):     # a checkout gives every file the same, later write time
        os.utime(p, (T0 + 5000, T0 + 5000))


@pytest.fixture
def demo(tmp_path, monkeypatch):
    runs = tmp_path / "runs"
    cache = runs / ".cache_run"
    _entry(cache, "a", 1.0, T0 + 10)    # written by the run
    _entry(cache, "b", 2.0, T0 + 110)   # written by its re-score
    atts = [("run", T0, "the run"), ("run/rescore", T0 + 100, "fresh re-score")]
    monkeypatch.setattr(spend, "RUNS", runs)
    monkeypatch.setattr(spend, "ENTRIES", tmp_path / "spend_entries.json")
    monkeypatch.setattr(spend, "attempts", lambda: {".cache_run": list(atts)})
    monkeypatch.setattr(spend, "REPORTED", {"run": ["run"]})
    return runs, cache


def test_recorded_entries_keep_their_attempt_after_a_checkout(demo):
    runs, cache = demo
    o, entries, problems = spend.main(recorded={})
    assert len(problems["unattributed"]) == 2       # no record and no journal: nothing is guessed
    _checkout(cache)
    o, entries, problems = spend.main(recorded=RECORDED)
    assert not any(problems.values()) and entries == RECORDED
    assert o["attempts"]["run"]["usd"] == 1.0 and o["attempts"]["run/rescore"]["usd"] == 2.0


def test_new_entries_go_to_the_attempt_whose_journal_lists_them(demo):
    runs, cache = demo
    c = _entry(cache, "c", 0.5, T0 + 300)
    d = _entry(cache, "d", 0.25, T0 + 310)
    _journal(runs / "run" / "rescore_s200.cache_writes.txt", c)
    _journal(runs / "_archive" / "run2_killed" / "cache_writes.txt", d)   # an archived attempt's own journal
    _checkout(cache)                    # file times play no part
    o, entries, problems = spend.main(recorded=RECORDED)
    assert not any(problems.values())
    assert entries[".cache_run"]["run/rescore_s200"] == [c[:16]]
    assert entries[".cache_run"]["_archive/run2_killed"] == [d[:16]]
    assert o["by_kind_usd"]["rescore"] == 2.5 and o["reported"]["run"]["usd_full"] == 1.0
    assert o["reported"]["run"]["usd_rescore_new_seeds"] == {"run/rescore_s200": 0.5}
    assert o["attempts"]["_archive/run2_killed"]["what"] == "found by its cache-write journal"


def test_entries_no_journal_lists_are_reported(demo):
    runs, cache = demo
    _entry(cache, "e", 0.3, T0 + 400)   # e.g. written by a process without a journal
    o, entries, problems = spend.main(recorded=RECORDED)
    assert len(problems["unattributed"]) == 1 and "eeeeeeeeeeeeeeee" in problems["unattributed"][0]
    assert o["attempts"]["run/rescore"]["usd"] == 2.0


def test_a_missing_recorded_entry_is_reported(demo):
    recorded = {".cache_run": {"run": ["aaaaaaaaaaaaaaaa"], "run/rescore": ["bbbbbbbbbbbbbbbb", "ffffffffffffffff"]}}
    o, entries, problems = spend.main(recorded=recorded)
    assert problems["missing"] == [".cache_run/ffffffffffffffff"]
