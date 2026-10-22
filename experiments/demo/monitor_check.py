"""Replay every shadow-monitor reading of the reported AgentQA runs offline and compare it with the trace.

    python experiments/demo/monitor_check.py [RUN ...]   # writes <run>/monitor_check.json, exits 1 on a difference
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from live_demo import OUT, _agentqa

from rsi.core import Artifact, CachedLLM, ClaudeCLI
from rsi.core.evaluate import Evaluator
from rsi.trace import load_trace

#: run directory -> its LLM cache (both under demo/runs)
RUNS = {
    "metaharness": ".cache_metaharness", "metaharness_hard": ".cache_metaharness_hard", "rrsi": ".cache_rrsi",
    "rrsi_hard": ".cache_rrsi_hard", "metaharness_a": "_archive/.cache_metaharness_before_fix",
    "_archive/metaharness_before_fix": "_archive/.cache_metaharness_before_fix",
    "_archive/metaharness_fix_v1": "_archive/.cache_metaharness_fix_v1",
    "_archive/metaharness_fix_v2": "_archive/.cache_metaharness_fix_v2",
}


def artifact(run_dir: Path, version: str, short_id: str) -> Artifact:
    """The monitored version: a Meta-Harness candidate's source, or an RRSI artifact file."""
    src = run_dir / "store" / "candidates" / version / "src"
    if src.is_dir():
        art = Artifact.from_dir(src)
    else:
        art = Artifact(json.loads(next((run_dir / "artifacts").glob(f"*/{short_id}*.json")).read_text())["files"])
    if art.short_id != short_id:
        raise SystemExit(f"{run_dir.name} {version}: rebuilt artifact {art.short_id} != recorded {short_id}")
    return art


def check(run: str) -> dict:
    d = OUT / run
    summ = d / "summary.json"
    suite = (json.loads(summ.read_text()).get("setup") or {}).get("suite") if summ.exists() else None
    llm = CachedLLM(ClaudeCLI("haiku", timeout_s=300), OUT / RUNS[run], offline=True)
    ev = Evaluator(_agentqa(suite or "easy"), llm, workers=4, allow_sealed=True)
    rows = []
    for e in load_trace(d):
        if e["kind"] != "monitor":
            continue
        data = e["data"]
        art = artifact(d, data["version"], data["artifact"])
        for split, rec in data["sealed"].items():
            r = ev.evaluate(art, split, 1)
            rows.append({"version": data["version"], "split": split, "recorded": rec["S"], "replayed": r.score,
                         "missing": r.n_missing, "matches": abs(r.score - rec["S"]) < 1e-9 and not r.n_missing})
    out = {"run": run, "readings": len(rows), "matches": all(r["matches"] for r in rows) and llm.misses == 0,
           "cache_hits": llm.hits, "cache_misses": llm.misses, "rows": rows}
    (d / "monitor_check.json").write_text(json.dumps(out, indent=1))
    print(f"{run:34s} {len(rows)} readings", "match" if out["matches"] else "DIFFER", "hits", llm.hits,
          "misses", llm.misses, flush=True)
    return out


if __name__ == "__main__":
    results = [check(r) for r in (sys.argv[1:] or RUNS)]
    if not all(r["matches"] for r in results):
        raise SystemExit(1)
