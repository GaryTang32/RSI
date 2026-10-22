"""spend.py assigns cache entries to attempts by record and journal, never by file time; live writers take a lock."""
import fcntl
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "experiments" / "demo"))

import leak_ablation  # noqa: E402
import live_demo  # noqa: E402
import rescore  # noqa: E402
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


@pytest.fixture
def writer(tmp_path, monkeypatch):
    """live_demo writing to ``tmp_path``, no lock taken yet."""
    monkeypatch.setattr(live_demo, "OUT", tmp_path)
    monkeypatch.setattr(live_demo, "_WRITER_LOCK", None)


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
    o2, entries2, problems2 = spend.main(recorded=entries)   # what --force wrote is read back the same way
    assert (o2, entries2) == (o, entries) and not any(problems2.values())


def test_entries_no_journal_lists_are_reported(demo):
    runs, cache = demo
    _entry(cache, "e", 0.3, T0 + 400)   # e.g. written by a process without a journal
    o, entries, problems = spend.main(recorded=RECORDED)
    assert len(problems["unattributed"]) == 1 and "eeeeeeeeeeeeeeee" in problems["unattributed"][0]
    assert o["attempts"]["run/rescore"]["usd"] == 2.0


def test_a_missing_recorded_entry_is_reported(demo):
    recorded = {".cache_run": {"run": ["aaaaaaaaaaaaaaaa"], "run/rescore": ["bbbbbbbbbbbbbbbb", "ffffffffffffffff"]}}
    assert spend.main(recorded=recorded)[2]["missing"] == [".cache_run/ffffffffffffffff"]


def test_a_recorded_journal_attempt_follows_its_journal_when_archived(demo):
    """--force recorded a journal-only attempt under its run's name, then --resume archived it with its journal and
    a new attempt took the name: the journals decide, so neither attempt is misnamed."""
    runs, cache = demo
    c = _entry(cache, "c", 0.5, T0 + 300)
    _journal(runs / "newrun" / "cache_writes.txt", c)
    o, entries, problems = spend.main(recorded=RECORDED)
    assert entries[".cache_run"]["newrun"] == [c[:16]]
    (runs / "_archive").mkdir()
    (runs / "newrun").rename(runs / "_archive" / "newrun_20260101T000000Z")
    d = _entry(cache, "d", 0.25, T0 + 400)
    _journal(runs / "newrun" / "cache_writes.txt", d)          # the resumed attempt
    o2, entries2, problems2 = spend.main(recorded=entries)
    assert not any(problems2.values())
    assert entries2[".cache_run"]["_archive/newrun_20260101T000000Z"] == [c[:16]]
    assert entries2[".cache_run"]["newrun"] == [d[:16]]


def test_resume_drops_journal_lines_of_entries_a_killed_attempt_never_wrote(tmp_path, monkeypatch, writer):
    """--resume archives the unclean attempt after dropping journal lines of entries that never appeared; a cache
    under _archive is resolved, an unknown one left alone."""
    monkeypatch.setattr(live_demo, "RESUME", True)
    written = _entry(tmp_path / ".cache_run", "a", 1.0, T0)
    old = _entry(tmp_path / "_archive" / ".cache_old", "c", 1.0, T0)
    run = tmp_path / "run"
    _journal(run / "cache_writes.txt", written, "b" * 64)          # killed before "b..." appeared
    (run / "summary.json").write_text(json.dumps({"clean": False}))
    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "rescore_s200.cache_writes.txt").write_text(f".cache_old/{old}\n.cache_old/{'d' * 64}\n"
                                                                   f".cache_gone/{'e' * 64}\n")
    assert live_demo._fresh("run") == run and not run.exists()
    archived = next((tmp_path / "_archive").glob("run_*"))
    assert (archived / "cache_writes.txt").read_text() == f".cache_run/{written}\n"
    assert (tmp_path / "x" / "rescore_s200.cache_writes.txt").read_text() == (f".cache_old/{old}\n"
                                                                            f".cache_gone/{'e' * 64}\n")


def test_every_live_writer_takes_the_lock_and_drops_dangling_lines(tmp_path, monkeypatch, writer):
    """A new run, not only --resume, drops what a killed writer left, and holds demo/runs/.live_writer.lock."""
    monkeypatch.setattr(live_demo, "RESUME", False)
    written = _entry(tmp_path / ".cache_run", "a", 1.0, T0)
    _journal(tmp_path / "old" / "rescore_s200.cache_writes.txt", written, "b" * 64)
    assert live_demo._fresh("newrun") == tmp_path / "newrun"
    assert (tmp_path / "old" / "rescore_s200.cache_writes.txt").read_text() == f".cache_run/{written}\n"
    probe = ("import fcntl, sys; f = open(sys.argv[1], 'w')\n"
             "try:\n    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB); print('free')\n"
             "except BlockingIOError:\n    print('held')")
    lock = str(tmp_path / ".live_writer.lock")
    held = subprocess.run([sys.executable, "-c", probe, lock], capture_output=True, text=True).stdout.strip()
    live_demo._WRITER_LOCK.close()
    assert held == "held"


class _Called(Exception):
    pass


def _stop(*_a, **_k):
    raise _Called


def _no_model(*_a, **_k):
    raise AssertionError("a test must never build a live model client")


def _rescore_run(tmp_path, monkeypatch, *, clean: bool | None = None) -> Path:
    run = tmp_path / "run"
    run.mkdir()
    summary = {"setup": {"suite": "easy"}, "seed_files": {}, "final_files": {}}
    (run / "summary.json").write_text(json.dumps(summary if clean is None else {"clean": clean, **summary}))
    (tmp_path / ".cache_run").mkdir()
    monkeypatch.setattr(rescore, "OUT", tmp_path)
    monkeypatch.setattr(rescore, "ClaudeCLI", _no_model)    # never a live call, even if the lock step went missing
    return run


def test_rescore_record_begins_a_live_write(tmp_path, monkeypatch):
    _rescore_run(tmp_path, monkeypatch)
    monkeypatch.setattr(rescore, "begin_live_write", _stop)
    with pytest.raises(_Called):
        rescore.rescore_run("run", 200, record=True)


def test_leak_check_record_begins_a_live_write(monkeypatch):
    monkeypatch.setattr(leak_ablation, "ClaudeCLI", _no_model)
    monkeypatch.setattr(leak_ablation, "begin_live_write", _stop)
    with pytest.raises(_Called):
        leak_ablation.run(9000, True)


def test_a_second_live_writer_is_refused_before_any_side_effect(tmp_path, monkeypatch, writer):
    """While another process holds the lock, --resume refuses before archiving the attempt or touching a journal,
    and --record before building a model client; each tries the lock once, without waiting."""
    monkeypatch.setattr(live_demo, "RESUME", True)
    run = _rescore_run(tmp_path, monkeypatch, clean=False)
    _journal(run / "cache_writes.txt", "b" * 64)                    # dangling: no such entry
    holder = open(tmp_path / ".live_writer.lock", "w")
    fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)               # another writer (a separate open file)
    real_flock, attempts = fcntl.flock, []

    def no_waiting(f, op):
        attempts.append(op)
        if not op & fcntl.LOCK_NB or len(attempts) > 3:
            raise AssertionError("a live writer waited for the lock instead of refusing")
        return real_flock(f, op)

    monkeypatch.setattr(fcntl, "flock", no_waiting)
    monkeypatch.setattr(leak_ablation, "ClaudeCLI", _no_model)
    try:
        with pytest.raises(SystemExit, match="one at a time"):
            live_demo._fresh("run")
        assert run.exists() and not (tmp_path / "_archive").exists()
        assert (run / "cache_writes.txt").read_text() == f".cache_run/{'b' * 64}\n"
        with pytest.raises(SystemExit, match="one at a time"):
            rescore.rescore_run("run", 200, record=True)
        with pytest.raises(SystemExit, match="one at a time"):
            leak_ablation.run(9000, True)
    finally:
        holder.close()
