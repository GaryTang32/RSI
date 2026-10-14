"""Account for every live Claude Haiku call the demo made: per LLM cache, per attempt, and per reported run.

Every successful live call is stored once in a run's LLM cache with its metered cost (``usage.cost_usd``); a call
that failed or timed out is not cached, so it is not counted here, whether or not it was billed. Several attempts
can share a cache: an attempt cut short by the usage cap or a container restart is archived and the run restarts
from the same cache, replaying the calls the earlier attempt paid for. A cache entry belongs to the attempt that was
running when the entry was written: the latest attempt that started at or before the file's write time. Attempt
start times come from each attempt's trace (or the ``started_at`` its JSON records); a run's original post-run
report (trial seeds from 0, superseded by ``rescore.py``) starts when its trace ends. The attempt list is kept by
hand: an attempt archived later (e.g. by ``live_demo.py --resume``) must be added to it.

Writes ``demo/spend.json``::

    python experiments/demo/spend.py

Write times are not kept by git, so run this on the machine that made the calls; the committed ``demo/spend.json``
is the record.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNS = ROOT / "demo" / "runs"


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
            ("_archive/metaharness_hard_capped", trace_start("_archive/metaharness_hard_capped"), "hard Meta-Harness, crashed"),
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


def main() -> dict:
    per_attempt: dict[str, dict] = {}
    caches = {}
    known = attempts()
    for c in sorted([*RUNS.glob(".cache_*"), *(RUNS / "_archive").glob(".cache_*")]):
        rel = str(c.relative_to(RUNS))
        files = [p for p in c.rglob("*.json") if p.is_file()]
        total = round(sum(entry_cost(p) for p in files), 4)
        caches[rel] = {"usd": total, "entries": len(files)}
        if not files or rel not in known:
            if total:
                raise SystemExit(f"{rel} has ${total} of calls but no attempt list")
            continue
        atts = sorted(known[rel], key=lambda x: x[1])
        for p in files:
            t = p.stat().st_mtime
            owner = next((a for a in reversed(atts) if a[1] <= t), atts[0])
            rec = per_attempt.setdefault(owner[0], {"cache": rel, "what": owner[2], "usd": 0.0, "entries": 0})
            rec["usd"] += entry_cost(p)
            rec["entries"] += 1
    for rec in per_attempt.values():
        rec["usd"] = round(rec["usd"], 4)
    reported = {}
    for run, atts in REPORTED.items():
        parts = {a: per_attempt.get(a, {}).get("usd", 0.0) for a in atts}
        reported[run] = {"usd_full": round(sum(parts.values()), 4), "attempts": parts,
                         "usd_rescore": per_attempt.get(f"{run}/rescore", {}).get("usd", 0.0),
                         "usd_original_report": per_attempt.get(f"{run}/report", {}).get("usd", 0.0)}
    by_kind = {}
    for name, rec in per_attempt.items():
        kind = name.rsplit("/", 1)[-1] if "/" in name and name.rsplit("/", 1)[-1] in ("rescore", "report") else None
        kind = kind or ("leak_check" if "leak_ablation" in name else "calibration" if name.startswith("calibration")
                        else "run")
        by_kind[kind] = by_kind.get(kind, 0.0) + rec["usd"]
    total_raw = sum(entry_cost(p) for c in [*RUNS.glob(".cache_*"), *(RUNS / "_archive").glob(".cache_*")]
                    for p in c.rglob("*.json") if p.is_file())
    out = {"method": __doc__.split("\n\n")[1].replace("\n", " "),
           "total_usd": round(total_raw, 4), "by_kind_usd": {k: round(v, 4) for k, v in by_kind.items()},
           "caches": caches, "attempts": per_attempt, "reported": reported}
    (ROOT / "demo" / "spend.json").write_text(json.dumps(out, indent=1))
    return out


if __name__ == "__main__":
    o = main()
    print("total", o["total_usd"])
    for r, v in o["reported"].items():
        print(f"{r:36s} full ${v['usd_full']:.4f}  re-score ${v['usd_rescore']:.4f}  {v['attempts']}")
    sys.exit(0)
