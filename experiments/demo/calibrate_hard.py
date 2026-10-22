"""Calibrate the hard AgentQA suite: does one simple trick (write Python and run it) already solve it?

    python experiments/demo/calibrate_hard.py   # replay at $0; exits 1 unless it matches _archive/calibrate_hard.json
"""
from __future__ import annotations

import json

from _demo_common import RUNS

from rsi.core import Artifact, CachedLLM, ClaudeCLI, transfer_report
from rsi.domains.agentqa import AgentQADomain
from rsi.domains.agentqa.hard import OOD_HINT, make_hard_suite

if __name__ == "__main__":
    dom = AgentQADomain(make_hard_suite(seed=0), ood_hint=OOD_HINT)
    # a hit is replayed whether or not the cache is offline, so a new calibration needs a new cache directory
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
