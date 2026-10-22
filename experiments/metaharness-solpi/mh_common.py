"""Helpers shared by the Meta-Harness experiments (M1-M10); the SoL-Pi scripts do not use this file."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np

from _common import RESULTS


def drop_traces(store_root: str | Path) -> None:
    """Delete a finished run's trace files (2-8 MB per candidate); the JSON results keep what is reported."""
    for d in Path(store_root).glob("candidates/*/eval/search/traces"):
        shutil.rmtree(d, ignore_errors=True)


def session_stats(store) -> dict:
    """Per-iteration means of the proposer's read accounting in ``sessions/iter*/meta.json``."""
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


def batch_seeds(batch: str, n_seeds: int) -> tuple[int, list[int]]:
    """``i/n`` -> (i, the seeds s < n_seeds with s % n == i)."""
    i, n = map(int, batch.split("/"))
    return i, [s for s in range(n_seeds) if s % n == i]


def load_parts(name: str) -> list[dict]:
    return [r for p in sorted(RESULTS.glob(f"{name}.part*.json")) for r in json.loads(p.read_text())]
