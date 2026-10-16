"""Calibrate the hard AgentQA suite: does any single simple trick already solve it?

Scores two fixed harnesses, one trial per task, on the default hard suite (``make_hard_suite(seed=0)``: 12 practice
tasks and 8 OOD tasks): the plain one-call seed, and a "write Python and run it" harness (Run 1's iteration-2
``code_execution`` candidate). The calls are cached in ``demo/runs/_archive/.cache_calib_hard``, so this replays the
recorded calibration at $0::

    python experiments/demo/calibrate_hard.py      # replay: demo/runs/_archive/calibrate_hard_replay.log

The replay compares every score and per-family score with ``_archive/calibrate_hard.json`` (transcribed from
``_archive/calibrate_hard.log``, the original live run's output, from an earlier copy of this script) and exits 1 on
any difference or cache miss.

(``_archive/.cache_calib_hard_v1`` holds a first calibration, of an earlier and easier version of the families,
kept for the spend total.)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rsi.core import Artifact, CachedLLM, ClaudeCLI, transfer_report  # noqa: E402
from rsi.domains.agentqa import AgentQADomain  # noqa: E402
from rsi.domains.agentqa.hard import OOD_HINT, make_hard_suite  # noqa: E402

RUNS = ROOT / "demo" / "runs"

if __name__ == "__main__":
    dom = AgentQADomain(make_hard_suite(seed=0), ood_hint=OOD_HINT)
    # offline: a cache miss is an error, never a paid call. A new calibration would need a new cache directory (this
    # one holds the recorded calls, and a hit is replayed whether or not the cache is offline).
    llm = CachedLLM(ClaudeCLI("haiku", timeout_s=300), RUNS / "_archive" / ".cache_calib_hard", offline=True)
    arms = {"seed": AgentQADomain.seed_artifact(),
            "code_exec": Artifact.from_dir(RUNS / "_archive" / "metaharness_before_fix" / "store" / "candidates"
                                           / "code_execution" / "src")}
    rep = transfer_report(dom, llm, arms, splits=("evolve", "ood"), k=1, workers=4)
    for s, row in rep["splits"].items():
        print(s, {a: round(v["S"], 3) for a, v in row.items()}, {a: v.get("families") for a, v in row.items()})
    print("usd", round(llm.meter.total().cost_usd, 3), "cache hits", llm.hits, "misses", llm.misses)
    want = json.loads((RUNS / "_archive" / "calibrate_hard.json").read_text())["splits"]
    got = {s: {a: {"S": round(v["S"], 3), "families": v.get("families")} for a, v in row.items()}
           for s, row in rep["splits"].items()}
    ok = got == want and llm.misses == 0
    print("matches the recorded calibration" if ok else "DIFFERS from the recorded calibration")
    if not ok:
        raise SystemExit(1)
