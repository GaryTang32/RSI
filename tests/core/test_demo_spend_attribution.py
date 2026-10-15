"""spend.py: recorded cache entries keep their attempt on any checkout; an unrecorded entry goes to a new attempt by
its write time, and is otherwise reported instead of guessed."""
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "experiments" / "demo"))

import spend  # noqa: E402

T0 = 1_700_000_000.0


def _entry(cache: Path, name: str, usd: float, mtime: float) -> None:
    p = cache / (name * 2) / f"{name * 64}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"usage": {"cost_usd": usd}}))
    os.utime(p, (mtime, mtime))


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
    return runs, cache, atts


def test_recorded_entries_keep_their_attempt_after_a_checkout(demo):
    runs, cache, _ = demo
    o, entries, problems = spend.main(recorded={})
    assert len(problems["unattributed"]) == 2       # no record: no write time is trusted
    recorded = {".cache_run": {"run": ["aaaaaaaaaaaaaaaa"], "run/rescore": ["bbbbbbbbbbbbbbbb"]}}
    for p in cache.rglob("*.json"):     # a checkout gives every file the same, later write time
        os.utime(p, (T0 + 5000, T0 + 5000))
    o, entries, problems = spend.main(recorded=recorded)
    assert not any(problems.values())
    assert entries == recorded
    assert o["attempts"]["run"]["usd"] == 1.0 and o["attempts"]["run/rescore"]["usd"] == 2.0


def test_a_new_attempt_after_the_checkout_owns_its_entries(demo):
    runs, cache, atts = demo
    recorded = {".cache_run": {"run": ["aaaaaaaaaaaaaaaa"], "run/rescore": ["bbbbbbbbbbbbbbbb"]}}
    for p in cache.rglob("*.json"):
        os.utime(p, (T0 + 5000, T0 + 5000))
    atts.append(("run/rescore_s200", T0 + 6000, "new trial seeds"))
    _entry(cache, "c", 0.5, T0 + 6010)
    o, entries, problems = spend.main(recorded=recorded)
    assert not any(problems.values())
    assert entries[".cache_run"]["run/rescore_s200"] == ["cccccccccccccccc"]
    assert o["by_kind_usd"]["rescore"] == 2.5 and o["reported"]["run"]["usd_full"] == 1.0
    assert o["reported"]["run"]["usd_rescore_new_seeds"] == {"run/rescore_s200": 0.5}


def test_unrecorded_entries_with_checkout_write_times_are_not_guessed(demo):
    runs, cache, atts = demo
    recorded = {".cache_run": {"run": ["aaaaaaaaaaaaaaaa"], "run/rescore": ["bbbbbbbbbbbbbbbb"]}}
    atts.append(("run/rescore_s200", T0 + 200, "new trial seeds"))
    _entry(cache, "c", 0.5, T0 + 210)
    for p in cache.rglob("*.json"):     # committed without refreshing the record, then checked out elsewhere
        os.utime(p, (T0 + 5000, T0 + 5000))
    o, entries, problems = spend.main(recorded=recorded)
    assert len(problems["unattributed"]) == 1 and "started before the newest recorded entry" in problems["unattributed"][0]


def test_an_entry_written_before_its_attempt_started_is_not_given_to_a_recorded_attempt(demo):
    runs, cache, atts = demo
    recorded = {".cache_run": {"run": ["aaaaaaaaaaaaaaaa"], "run/rescore": ["bbbbbbbbbbbbbbbb"]}}
    _entry(cache, "d", 0.3, T0 + 300)   # a killed attempt that never wrote its record
    atts.append(("run/rescore_s200", T0 + 400, "new trial seeds"))
    o, entries, problems = spend.main(recorded=recorded)
    assert len(problems["unattributed"]) == 1 and "run/rescore has recorded entries" in problems["unattributed"][0]
    assert o["attempts"]["run/rescore"]["usd"] == 2.0


def test_a_missing_recorded_entry_is_reported(demo):
    runs, cache, _ = demo
    recorded = {".cache_run": {"run": ["aaaaaaaaaaaaaaaa"], "run/rescore": ["bbbbbbbbbbbbbbbb", "eeeeeeeeeeeeeeee"]}}
    o, entries, problems = spend.main(recorded=recorded)
    assert problems["missing"] == [".cache_run/eeeeeeeeeeeeeeee"]
