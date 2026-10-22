"""Account for every live Claude Haiku call the demo made: per LLM cache, per attempt, and per reported run.

    python experiments/demo/spend.py [--force]   # compare with demo/spend.json (exit 1 if it differs); --force rewrites
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from _demo_common import DEMO, JOURNAL, RUNS

ENTRIES = DEMO / "spend_entries.json"
#: how entries are attributed; recorded in spend.json
METHOD = (
    "Every successful live call is stored once in a run's LLM cache with its metered cost (``usage.cost_usd``); a call "
    "that failed or timed out is not cached, so it is not counted here, whether or not it was billed. Several attempts "
    "can share a cache: an attempt cut short by the usage cap or a container restart is archived and the run restarts "
    "from the same cache, replaying the calls the earlier attempt paid for. A cache entry belongs to the attempt that "
    "was running when the entry was written: the latest attempt that started at or before the file's write time. "
    "Attempt start times come from each attempt's trace (or the ``started_at`` its JSON records); a run's original "
    "post-run report (trial seeds from 0, superseded by ``rescore.py``) starts when its trace ends. The attempt list "
    "is kept by hand. Git keeps no write times, so that attribution, made on the machine that made the calls, is "
    "recorded entry by entry in ``demo/spend_entries.json``, and a recorded entry keeps its attempt on any checkout. "
    "Entries written since are attributed without write times, by the cache-write journal of the attempt that wrote "
    "them (``CachedLLM(journal= ...)``: ``<run>/cache_writes.txt`` for a run, ``<run>/<record>.cache_writes.txt`` for "
    "a re-score or leak check), which moves with an archived attempt and wins over the record; an entry neither lists "
    "is reported as unattributed.")
REPORT = "original post-run report (trial seeds from 0; superseded by the fresh re-score)"
#: runs whose re-scores and leak checks with new trial seeds (rescore_s<N>.json, leak_ablation_s<N>.json) are attempts
NEW_SEED_RUNS = ("metaharness", "metaharness_hard", "rrsi", "rrsi_hard", "_archive/metaharness_before_fix",
                 "_archive/metaharness_fix_v1", "_archive/metaharness_fix_v2")
#: reported result -> the attempts whose calls its trajectory used
REPORTED = {
    "metaharness": ["_archive/metaharness_v3_capped", "metaharness"],
    "metaharness_hard": ["_archive/metaharness_hard_capped", "_archive/metaharness_hard_restart1",
                         "_archive/metaharness_hard_restart2", "_archive/metaharness_hard_capped2", "metaharness_hard"],
    "rrsi": ["_archive/rrsi_restarted", "rrsi"],
    "rrsi_hard": ["_archive/rrsi_hard_capped", "rrsi_hard"],
    "autoresearch": ["autoresearch"],
    "metaharness_a": ["metaharness_a"],
    "_archive/metaharness_before_fix": ["_archive/metaharness_before_fix"],
    "_archive/metaharness_fix_v1": ["_archive/metaharness_fix_v1"],
    "_archive/metaharness_fix_v2": ["_archive/metaharness_fix_v2"],
}


def trace_start(run: str) -> float:
    with open(RUNS / run / "trace.jsonl") as f:
        return float(json.loads(f.readline())["t"])


def trace_end(run: str) -> float:
    *_, last = (RUNS / run / "trace.jsonl").read_text().strip().splitlines()
    return float(json.loads(last)["t"])


def started(path: str) -> float | None:
    p = RUNS / path
    return float(json.loads(p.read_text())["started_at"]) if p.exists() else None


def _run(run: str, what: str) -> tuple:
    return run, trace_start(run), what


def _report(run: str, what: str = REPORT) -> tuple:
    return f"{run}/report", trace_end(run), what


def _rescore(run: str) -> tuple:
    return f"{run}/rescore", started(f"{run}/rescore.json"), "fresh re-score"


def attempts() -> dict[str, list[tuple[str, float, str]]]:
    """cache -> [(attempt, start time, what it was)], in start order."""
    a = {
        ".cache_metaharness": [
            _run("_archive/metaharness_v3_capped", "Run 4, cut off by the usage cap"),
            _run("metaharness", "Run 4, resumed (reported)"), _report("metaharness"), _rescore("metaharness")],
        ".cache_metaharness_hard": [
            _run("_archive/metaharness_hard_capped", "hard Meta-Harness, usage cap"),
            _run("_archive/metaharness_hard_restart1", "container restart"),
            _run("_archive/metaharness_hard_restart2", "container restart"),
            _run("_archive/metaharness_hard_capped2", "usage cap"),
            _run("metaharness_hard", "hard Meta-Harness (reported)"), _report("metaharness_hard"),
            # the first leak check recorded no start time; it ran after the run's own report, which made no new calls
            ("metaharness_hard/leak_ablation_v1", trace_end("metaharness_hard") + 120, "first leak check (superseded)"),
            _rescore("metaharness_hard"),
            ("metaharness_hard/leak_ablation", started("metaharness_hard/leak_ablation.json"), "leak check")],
        ".cache_rrsi": [_run("_archive/rrsi_restarted", "RRSI, restarted"), _run("rrsi", "RRSI (reported)"),
                        _report("rrsi"), _rescore("rrsi")],
        ".cache_rrsi_hard": [_run("_archive/rrsi_hard_capped", "hard RRSI, crashed in calibration"),
                             _run("rrsi_hard", "hard RRSI (reported)"), _report("rrsi_hard"), _rescore("rrsi_hard")],
        ".cache_autoresearch": [_run("autoresearch", "autoresearch (reported)")],
        "_archive/.cache_metaharness_before_fix": [
            _run("metaharness_a", "replicate, interrupted during its report"),
            _report("metaharness_a", REPORT + ", interrupted"),
            _run("_archive/metaharness_before_fix", "Run 1"), _report("_archive/metaharness_before_fix"),
            _rescore("_archive/metaharness_before_fix")],
        "_archive/.cache_metaharness_fix_v1": [_run("_archive/metaharness_fix_v1", "Run 2"),
                                               _report("_archive/metaharness_fix_v1"),
                                               _rescore("_archive/metaharness_fix_v1")],
        "_archive/.cache_metaharness_fix_v2": [_run("_archive/metaharness_fix_v2", "Run 3"),
                                               _report("_archive/metaharness_fix_v2"),
                                               _rescore("_archive/metaharness_fix_v2")],
        "_archive/.cache_calib_hard_v1": [("calibration_v1", 0.0, "first hard-suite calibration (earlier families)")],
        "_archive/.cache_calib_hard": [("calibration", 0.0, "hard-suite calibration")],
    }
    for run in NEW_SEED_RUNS:
        cache = str(Path(run).parent / f".cache_{Path(run).name}")   # live_demo's cache naming
        for f in sorted((RUNS / run).glob("rescore_s*.json")) + sorted((RUNS / run).glob("leak_ablation_s*.json")):
            if not f.stem.endswith("_check"):
                a[cache].append((f"{run}/{f.stem}", started(f"{run}/{f.name}"),
                                 "re-score or leak check, new trial seeds"))
    return {c: [x for x in xs if x[1] is not None] for c, xs in a.items()}


def entry_cost(p: Path) -> float:
    try:
        d = json.loads(p.read_text())
    except (ValueError, UnicodeDecodeError):
        return 0.0
    if not isinstance(d, dict):
        return 0.0
    u = d.get("usage")
    return float((u.get("cost_usd") if isinstance(u, dict) else None) or d.get("cost_usd") or 0.0)


def entry_key(p: Path) -> str:
    """A cache entry's name in ``spend_entries.json``: the start of its prompt hash (unique within a cache)."""
    return p.stem[:16]


def journal_owners() -> dict[tuple[str, str], str]:
    """(cache directory name, entry key) -> the attempt whose cache-write journal lists the entry.

    ``<run>/cache_writes.txt`` is the attempt ``<run>``, ``<run>/<record>.cache_writes.txt`` the attempt
    ``<run>/<record>``; if two journals list an entry, the first in path order keeps it."""
    owners: dict[tuple[str, str], str] = {}
    for j in sorted(RUNS.rglob(f"*{JOURNAL}")):
        run = str(j.parent.relative_to(RUNS))
        att = run if j.name == JOURNAL else f"{run}/{j.name[:-len(JOURNAL) - 1]}"
        for line in j.read_text().splitlines():
            cache, _, key = line.partition("/")
            owners.setdefault((cache, key[:16]), att)
    return owners


def _caches() -> list[Path]:
    return [*RUNS.glob(".cache_*"), *(RUNS / "_archive").glob(".cache_*")]


def _kind(attempt: str) -> str:
    tail = attempt.rsplit("/", 1)[-1] if "/" in attempt else ""
    if tail.startswith("rescore"):
        return "rescore"
    if tail == "report":
        return "report"
    if tail.startswith("leak_ablation"):
        return "leak_check"
    return "calibration" if attempt.startswith("calibration") else "run"


def main(recorded: dict | None = None) -> tuple[dict, dict, dict]:
    """(spend record, {cache: {attempt: [entry keys]}}, {"missing": [...], "unattributed": [...]})."""
    if recorded is None:
        recorded = json.loads(ENTRIES.read_text()) if ENTRIES.exists() else {}
    owner_of = {(c, k): a for c, by in recorded.items() for a, ks in by.items() for k in ks}
    journaled = journal_owners()
    known = attempts()
    # attempts found by a journal stay valid once --force has recorded their entries
    by_journal = {a: "found by its cache-write journal" for a in set(journaled.values())}
    per_attempt: dict[str, dict] = {}
    caches, membership, missing, unattributed = {}, {}, [], []
    for c in sorted(_caches()):
        rel = str(c.relative_to(RUNS))
        files = [p for p in c.rglob("*.json") if p.is_file()]
        total = round(sum(entry_cost(p) for p in files), 4)
        caches[rel] = {"usd": total, "entries": len(files)}
        if not files or (rel not in known and not total):   # e.g. the tinylm data cache: no model calls
            continue
        what = {**by_journal, **{a[0]: a[2] for a in known.get(rel, [])}}
        present = set()
        for p in files:
            k = entry_key(p)
            present.add(k)
            # a journal moves with an archived attempt, so it wins over the record (which covers pre-journal entries)
            name = journaled.get((c.name, k)) or owner_of.get((rel, k))
            if name is None:
                unattributed.append(f"{rel}/{k}: in neither the record nor any attempt's cache-write journal")
                continue
            if name not in what:
                raise SystemExit(f"{rel}: recorded attempt {name!r} is in neither the attempt list nor a journal")
            rec = per_attempt.setdefault(name, {"cache": rel, "what": what[name], "usd": 0.0, "entries": 0})
            rec["usd"] += entry_cost(p)
            rec["entries"] += 1
            membership.setdefault(rel, {}).setdefault(name, []).append(k)
        missing += [f"{rel}/{k}" for (c2, k) in owner_of if c2 == rel and k not in present]
    missing += [f"{c2}/{k}" for (c2, k) in owner_of if c2 not in membership]
    for rec in per_attempt.values():
        rec["usd"] = round(rec["usd"], 4)
    membership = {c: {a: sorted(ks) for a, ks in sorted(by.items())} for c, by in sorted(membership.items())}
    reported = {}
    for run, atts in REPORTED.items():
        parts = {a: per_attempt.get(a, {}).get("usd", 0.0) for a in atts}
        reported[run] = {"usd_full": round(sum(parts.values()), 4), "attempts": parts,
                         "usd_rescore": per_attempt.get(f"{run}/rescore", {}).get("usd", 0.0),
                         "usd_original_report": per_attempt.get(f"{run}/report", {}).get("usd", 0.0)}
        new_seeds = {a: r["usd"] for a, r in per_attempt.items() if a.startswith(f"{run}/rescore_s")}
        if new_seeds:
            reported[run]["usd_rescore_new_seeds"] = new_seeds
    by_kind = {}
    for name, rec in per_attempt.items():
        kind = _kind(name)
        by_kind[kind] = by_kind.get(kind, 0.0) + rec["usd"]
    total_raw = sum(entry_cost(p) for c in _caches() for p in c.rglob("*.json") if p.is_file())
    out = {"method": METHOD, "total_usd": round(total_raw, 4),
           "by_kind_usd": {k: round(v, 4) for k, v in by_kind.items()},
           "caches": caches, "attempts": per_attempt, "reported": reported}
    return out, membership, {"missing": missing, "unattributed": unattributed}


if __name__ == "__main__":
    o, entries, problems = main()
    dest = DEMO / "spend.json"
    old = json.loads(dest.read_text()) if dest.exists() else None
    old_entries = json.loads(ENTRIES.read_text()) if ENTRIES.exists() else None
    for kind, why in (("missing", "recorded entries missing from the caches"),
                      ("unattributed", "entries no record or journal attributes (written without a journal?)")):
        if problems[kind]:
            print(f"{why}: {len(problems[kind])}", *problems[kind][:5], sep="\n  ")
    same = old == o and old_entries == entries and not any(problems.values())
    if "--force" in sys.argv and any(problems.values()):
        print("not writing: fix the entries above first")
    elif "--force" in sys.argv:
        dest.write_text(json.dumps(o, indent=1))
        ENTRIES.write_text(json.dumps(entries, indent=0, separators=(",", ":")) + "\n")
        print("wrote", dest, "and", ENTRIES, "" if old == o and old_entries == entries else "(changed)")
        same = True
    else:
        print("matches the committed record" if same else "DIFFERS from the committed record")
    print("total", o["total_usd"])
    for r, v in o["reported"].items():
        print(f"{r:36s} full ${v['usd_full']:.4f}  re-score ${v['usd_rescore']:.4f}  {v['attempts']}")
    sys.exit(0 if same else 1)
