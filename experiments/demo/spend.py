"""Account for every live Claude Haiku call the demo made: per LLM cache, per attempt, and per reported run.

Every successful live call is stored once in a run's LLM cache with its metered cost (``usage.cost_usd``); a call
that failed or timed out is not cached, so it is not counted here, whether or not it was billed. Several attempts
can share a cache: an attempt cut short by the usage cap or a container restart is archived and the run restarts
from the same cache, replaying the calls the earlier attempt paid for. A cache entry belongs to the attempt that was
running when the entry was written: the latest attempt that started at or before the file's write time. Attempt
start times come from each attempt's trace (or the ``started_at`` its JSON records); a run's original post-run
report (trial seeds from 0, superseded by ``rescore.py``) starts when its trace ends. The attempt list is kept by
hand. Git keeps no write times, so that attribution, made on the machine that made the calls, is recorded entry by
entry in ``demo/spend_entries.json``, and a recorded entry keeps its attempt on any checkout. Entries written since
are attributed without write times, by the cache-write journal of the attempt that wrote them (``CachedLLM(journal=
...)``: ``<run>/cache_writes.txt`` for a run, ``<run>/<record>.cache_writes.txt`` for a re-score or leak check),
which moves with an archived attempt and wins over the record; an entry neither lists is reported as unattributed.

Checks or writes ``demo/spend.json`` and ``demo/spend_entries.json``::

    python experiments/demo/spend.py            # recompute and compare with the committed record; exits 1 if it differs
    python experiments/demo/spend.py --force    # rewrite the record (an entry keeps its attempt unless a journal
                                                #  names another: a journal moves with an archived attempt)

``--force`` refuses while a recorded entry is missing from the caches or an entry is unattributed.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNS = ROOT / "demo" / "runs"
ENTRIES = ROOT / "demo" / "spend_entries.json"
JOURNAL = "cache_writes.txt"   # live_demo.JOURNAL


def trace_start(run: str) -> float:
    with open(RUNS / run / "trace.jsonl") as f:
        return float(json.loads(f.readline())["t"])


def trace_end(run: str) -> float:
    *_, last = (RUNS / run / "trace.jsonl").read_text().strip().splitlines()
    return float(json.loads(last)["t"])


def started(path: str) -> float | None:
    p = RUNS / path
    return float(json.loads(p.read_text())["started_at"]) if p.exists() else None


REPORT = "original post-run report (trial seeds from 0; superseded by the fresh re-score)"


def attempts() -> dict[str, list[tuple[str, float, str]]]:
    """cache -> [(attempt, start time, what it was)], in start order."""
    a = {
        ".cache_metaharness": [
            ("_archive/metaharness_v3_capped", trace_start("_archive/metaharness_v3_capped"), "Run 4, cut off by the usage cap"),
            ("metaharness", trace_start("metaharness"), "Run 4, resumed (reported)"),
            ("metaharness/report", trace_end("metaharness"), REPORT),
            ("metaharness/rescore", started("metaharness/rescore.json"), "fresh re-score")],
        ".cache_metaharness_hard": [
            ("_archive/metaharness_hard_capped", trace_start("_archive/metaharness_hard_capped"), "hard Meta-Harness, usage cap"),
            ("_archive/metaharness_hard_restart1", trace_start("_archive/metaharness_hard_restart1"), "container restart"),
            ("_archive/metaharness_hard_restart2", trace_start("_archive/metaharness_hard_restart2"), "container restart"),
            ("_archive/metaharness_hard_capped2", trace_start("_archive/metaharness_hard_capped2"), "usage cap"),
            ("metaharness_hard", trace_start("metaharness_hard"), "hard Meta-Harness (reported)"),
            ("metaharness_hard/report", trace_end("metaharness_hard"), REPORT),
            # the first leak check recorded no start time; it ran after the run's own report, which made no new calls
            ("metaharness_hard/leak_ablation_v1", trace_end("metaharness_hard") + 120, "first leak check (superseded)"),
            ("metaharness_hard/rescore", started("metaharness_hard/rescore.json"), "fresh re-score"),
            ("metaharness_hard/leak_ablation", started("metaharness_hard/leak_ablation.json"), "leak check")],
        ".cache_rrsi": [
            ("_archive/rrsi_restarted", trace_start("_archive/rrsi_restarted"), "RRSI, restarted"),
            ("rrsi", trace_start("rrsi"), "RRSI (reported)"),
            ("rrsi/report", trace_end("rrsi"), REPORT),
            ("rrsi/rescore", started("rrsi/rescore.json"), "fresh re-score")],
        ".cache_rrsi_hard": [
            ("_archive/rrsi_hard_capped", trace_start("_archive/rrsi_hard_capped"), "hard RRSI, crashed in calibration"),
            ("rrsi_hard", trace_start("rrsi_hard"), "hard RRSI (reported)"),
            ("rrsi_hard/report", trace_end("rrsi_hard"), REPORT),
            ("rrsi_hard/rescore", started("rrsi_hard/rescore.json"), "fresh re-score")],
        ".cache_autoresearch": [("autoresearch", trace_start("autoresearch"), "autoresearch (reported)")],
        "_archive/.cache_metaharness_before_fix": [
            ("metaharness_a", trace_start("metaharness_a"), "replicate, interrupted during its report"),
            ("metaharness_a/report", trace_end("metaharness_a"), REPORT + ", interrupted"),
            ("_archive/metaharness_before_fix", trace_start("_archive/metaharness_before_fix"), "Run 1"),
            ("_archive/metaharness_before_fix/report", trace_end("_archive/metaharness_before_fix"), REPORT),
            ("_archive/metaharness_before_fix/rescore", started("_archive/metaharness_before_fix/rescore.json"),
             "fresh re-score")],
        "_archive/.cache_metaharness_fix_v1": [
            ("_archive/metaharness_fix_v1", trace_start("_archive/metaharness_fix_v1"), "Run 2"),
            ("_archive/metaharness_fix_v1/report", trace_end("_archive/metaharness_fix_v1"), REPORT),
            ("_archive/metaharness_fix_v1/rescore", started("_archive/metaharness_fix_v1/rescore.json"), "fresh re-score")],
        "_archive/.cache_metaharness_fix_v2": [
            ("_archive/metaharness_fix_v2", trace_start("_archive/metaharness_fix_v2"), "Run 3"),
            ("_archive/metaharness_fix_v2/report", trace_end("_archive/metaharness_fix_v2"), REPORT),
            ("_archive/metaharness_fix_v2/rescore", started("_archive/metaharness_fix_v2/rescore.json"), "fresh re-score")],
        "_archive/.cache_calib_hard_v1": [("calibration_v1", 0.0, "first hard-suite calibration (earlier families)")],
        "_archive/.cache_calib_hard": [("calibration", 0.0, "hard-suite calibration")],
    }
    # re-scores and leak checks with new trial seeds (rescore_s<N>.json, leak_ablation_s<N>.json) are attempts too
    cache_of = {"metaharness": ".cache_metaharness", "metaharness_hard": ".cache_metaharness_hard", "rrsi": ".cache_rrsi",
                "rrsi_hard": ".cache_rrsi_hard", "_archive/metaharness_before_fix": "_archive/.cache_metaharness_before_fix",
                "_archive/metaharness_fix_v1": "_archive/.cache_metaharness_fix_v1",
                "_archive/metaharness_fix_v2": "_archive/.cache_metaharness_fix_v2"}
    for run, cache in cache_of.items():
        for f in sorted((RUNS / run).glob("rescore_s*.json")) + sorted((RUNS / run).glob("leak_ablation_s*.json")):
            if not f.stem.endswith("_check"):
                a[cache].append((f"{run}/{f.stem}", started(f"{run}/{f.name}"), "re-score or leak check, new trial seeds"))
    return {c: [x for x in xs if x[1] is not None] for c, xs in a.items()}


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


def entry_cost(p: Path) -> float:
    try:
        d = json.loads(p.read_text())
    except (ValueError, UnicodeDecodeError):
        return 0.0
    if not isinstance(d, dict):
        return 0.0
    u = d.get("usage")
    return float(((u or {}).get("cost_usd") if isinstance(u, dict) else None) or d.get("cost_usd") or 0.0)


def entry_key(p: Path) -> str:
    """A cache entry's name in ``spend_entries.json``: the start of its prompt hash (unique within a cache)."""
    return p.stem[:16]


def journal_owners() -> dict[tuple[str, str], str]:
    """(cache directory name, entry key) -> the attempt whose cache-write journal lists the entry:
    ``<run>/cache_writes.txt`` is the attempt ``<run>``, ``<run>/<record>.cache_writes.txt`` the attempt
    ``<run>/<record>``. A journal line is written just before its entry appears, so an attempt killed in between lists
    an entry it never wrote; every live writer drops such lines before it starts
    (``live_demo.drop_unwritten_journal_lines``). (Two journals list one entry only if two processes raced to write
    it; the first, in path order, keeps it.)"""
    owners: dict[tuple[str, str], str] = {}
    for j in sorted(RUNS.rglob(f"*{JOURNAL}")):
        run = str(j.parent.relative_to(RUNS))
        att = run if j.name == JOURNAL else f"{run}/{j.name[:-len(JOURNAL) - 1]}"
        for line in j.read_text().splitlines():
            cache, _, key = line.partition("/")
            owners.setdefault((cache, key[:16]), att)
    return owners


def main(recorded: dict | None = None) -> tuple[dict, dict, dict]:
    """(spend record, entry attribution {cache: {attempt: [entry keys]}}, problems {"missing": [...],
    "unattributed": [...]})."""
    if recorded is None:
        recorded = json.loads(ENTRIES.read_text()) if ENTRIES.exists() else {}
    owner_of = {(c, k): a for c, by in recorded.items() for a, ks in by.items() for k in ks}
    journaled = journal_owners()
    all_caches = sorted([*RUNS.glob(".cache_*"), *(RUNS / "_archive").glob(".cache_*")])
    per_attempt: dict[str, dict] = {}
    caches, membership, missing, unattributed = {}, {}, [], []
    known = attempts()
    for c in all_caches:
        rel = str(c.relative_to(RUNS))
        files = [p for p in c.rglob("*.json") if p.is_file()]
        total = round(sum(entry_cost(p) for p in files), 4)
        caches[rel] = {"usd": total, "entries": len(files)}
        if not files or (rel not in known and not total):   # e.g. the tinylm data cache: no model calls
            continue
        # attempts found by a journal stay valid once --force has recorded their entries
        what = {**{a: "found by its cache-write journal" for a in set(journaled.values())},
                **{a[0]: a[2] for a in known.get(rel, [])}}
        present = set()
        for p in files:
            k = entry_key(p)
            present.add(k)
            # a journal names the attempt that wrote the entry and moves with it (an attempt archived by --resume),
            # so it wins over the name the record kept; the record alone covers entries from before journals
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
        tail = name.rsplit("/", 1)[-1] if "/" in name else ""
        kind = ("rescore" if tail.startswith("rescore") else "report" if tail == "report"
                else "leak_check" if tail.startswith("leak_ablation") else "calibration" if name.startswith("calibration")
                else "run")
        by_kind[kind] = by_kind.get(kind, 0.0) + rec["usd"]
    total_raw = sum(entry_cost(p) for c in [*RUNS.glob(".cache_*"), *(RUNS / "_archive").glob(".cache_*")]
                    for p in c.rglob("*.json") if p.is_file())
    out = {"method": __doc__.split("\n\n")[1].replace("\n", " "),
           "total_usd": round(total_raw, 4), "by_kind_usd": {k: round(v, 4) for k, v in by_kind.items()},
           "caches": caches, "attempts": per_attempt, "reported": reported}
    return out, membership, {"missing": missing, "unattributed": unattributed}


if __name__ == "__main__":
    o, entries, problems = main()
    dest = ROOT / "demo" / "spend.json"
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
