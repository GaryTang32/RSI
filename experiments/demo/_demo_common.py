"""Paths and helpers shared by the live demo scripts (importing it puts the checkout's ``rsi`` on the path)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DEMO = ROOT / "demo"
RUNS = DEMO / "runs"
#: an attempt's cache-write journal: every cache entry it paid for (spend.py attributes entries by it)
JOURNAL = "cache_writes.txt"


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def per_arm(rep: dict, key: str = "S") -> dict:
    """``{split: {arm: value}}`` of one field of a transfer report."""
    return {s: {a: v[key] for a, v in row.items()} for s, row in rep["splits"].items()}


def traces_identical(src: Path, out: Path) -> dict:
    """Byte-compare every candidate's execution traces between a recorded run and its replay."""
    def traces(d: Path) -> dict:
        return {p.relative_to(d): p.read_bytes() for p in (d / "store" / "candidates").glob("*/eval/*/traces/*")}

    a, b = traces(src), traces(out)
    # the replay writes every trace afresh, so a trace only it has means the recorded run lost one
    return {"files": len(a), "identical": sum(1 for k in a if b.get(k) == a[k]),
            "missing_in_replay": len(set(a) - set(b)), "extra_in_replay": len(set(b) - set(a))}
