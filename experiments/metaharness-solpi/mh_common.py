"""Helpers shared by the Meta-Harness experiments (M1-M6); the SoL-Pi scripts do not use this file.

* ``drop_traces``: full MemoClassify traces hold every prompt and raw reply (2-8 MB per candidate), so a
  finished job deletes its run's trace files after its numbers are extracted (the JSON results keep
  everything the experiments report);
* ``session_stats``: per-iteration read accounting from ``sessions/iter*/meta.json``.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np


def drop_traces(store_root: str | Path) -> None:
    for d in Path(store_root).glob("candidates/*/eval/search/traces"):
        shutil.rmtree(d, ignore_errors=True)


def session_stats(store) -> dict:
    """Mean per iteration: files read (what the proposal was based on), files scanned (parsed for
    bookkeeping), trace files read, distinct candidates whose traces were read, chars."""
    rows = []
    for p in sorted(store.sessions_dir().glob("iter*/meta.json")):
        m = json.loads(p.read_text())
        rows.append({"read": m.get("n_files_read", 0), "scanned": m.get("n_files_scanned", 0),
                     "traces": (m.get("files_read_by_kind") or {}).get("traces", 0),
                     "trace_cands": len({f.split("/")[1] for f in m.get("files_read", []) if "/traces/" in f}),
                     "view_chars": m.get("view_chars", 0), "read_chars": m.get("read_chars", 0),
                     "scanned_chars": m.get("scanned_chars", 0), "reports": len(m.get("reports_written", []))})
    if not rows:
        return {}
    return {f"{k}_per_iter": float(np.mean([r[k] for r in rows])) for k in rows[0]}
