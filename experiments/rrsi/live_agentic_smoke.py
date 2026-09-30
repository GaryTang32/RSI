"""Live smoke (retry round 2, P10): the released code's agentic protocols with a real LLM (claims M4, M28).

AgentQA (suite seed 0, 12 evolve tasks), SimModel as the frozen task model, Claude Haiku (``claude -p``, fresh
cache) as proposer, critic, batch analyst and digesters, with ``proposer_protocol="json_actions"`` (the code's
40-turn strict-JSON action agent) and ``analyst="agentic"`` (the code's batch analyst dispatching read-only,
tool-using digesters). T = 1, m = 1, a USD cap. A demonstration that the ported protocols run end to end
with a real model: every proposer turn, digester turn and analyst turn is recorded and summarized.

    python experiments/rrsi/live_agentic_smoke.py --cache-dir DIR [--max-usd 1.0] [--cache-only]
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import save  # noqa: E402
from live_smoke import _RefuseCalls  # noqa: E402

from rsi.core import Budget, CachedLLM, ClaudeCLI  # noqa: E402
from rsi.domains.agentqa import AgentQADomain, SimModel, make_suite  # noqa: E402
from rsi.rrsi import Config, paired_transfer, run  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="live smoke of the agentic protocols")
    ap.add_argument("--cache-dir", required=True)
    ap.add_argument("--model", default="haiku")
    ap.add_argument("--max-usd", type=float, default=1.0)
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--cache-only", action="store_true")
    a = ap.parse_args()
    suite = make_suite(n_evolve=12, n_holdout=12, n_ood_per_family=3, seed=0)
    dom = AgentQADomain(suite)
    inner = _RefuseCalls(ClaudeCLI(a.model).name) if a.cache_only else ClaudeCLI(a.model, timeout_s=300)
    llm = CachedLLM(inner, a.cache_dir)
    cfg = Config(T=1, m=1, m_draft=0, k=2, workers=4, proposer_protocol="json_actions", analyst="agentic",
                 n_fail_traces=4, n_success_traces=2, repair_rounds=2, record_timestamps=False)
    out_dir = Path(a.out_dir or Path(a.cache_dir).parent / "live_agentic_run")
    t0 = time.time()
    res = run(dom, AgentQADomain.seed_artifact(), llm_task=SimModel(suite), llm_propose=llm, config=cfg,
              out_dir=out_dir, budget=Budget(max_usd=a.max_usd, max_wall_s=2400), verbose=True)
    rep = paired_transfer(dom, SimModel(suite), {"H0": res.baseline, "final": res.best}, k=2)
    trace = [dict(json.loads(l).get("data") or {}, kind=json.loads(l).get("kind"))
             for l in (out_dir / "trace.jsonl").read_text().splitlines() if l.strip()] \
        if (out_dir / "trace.jsonl").exists() else []
    actions = collections.Counter()
    for ev in trace:
        if ev.get("kind") == "proposal":
            try:
                actions[json.loads(ev.get("reply") or "{}").get("action", "?")] += 1
            except (json.JSONDecodeError, AttributeError):
                actions["(unparseable)"] += 1
    an = [ev for ev in trace if ev.get("kind") == "analysis"]
    calls = an[0].get("llm_calls", []) if an else []
    dig_actions = collections.Counter()
    for c in calls:
        if c.get("role") == "digester":
            try:
                dig_actions[json.loads(c.get("reply") or "{}").get("action", "?")] += 1
            except (json.JSONDecodeError, AttributeError):
                dig_actions["(unparseable)"] += 1
    report = json.loads((out_dir / "r0" / "analysis_report.json").read_text()) \
        if (out_dir / "r0" / "analysis_report.json").exists() else {}
    prop = json.loads((out_dir / "r0" / "A" / "proposal.json").read_text()) \
        if (out_dir / "r0" / "A" / "proposal.json").exists() else {}
    out = {"experiment": "live smoke of the agentic protocols (retry round 2: P10)", "model": a.model,
           "config": cfg.dump(), "stop_reason": res.stop_reason, "trajectory": res.trajectory,
           "usage": res.usage, "spend": res.meta.get("spend"),
           "proposer": {"status": prop.get("status"), "n_edits": prop.get("n_edits"),
                        "edits": [{k: e.get(k) for k in ("id", "component", "hypothesis")} for e in prop.get("edits", [])],
                        "json_actions_by_type": dict(actions)},
           "analyst": {"n_digests": report.get("n_digests"), "failure_modes": [m.get("mode") for m in
                                                                               report.get("failure_modes", [])],
                       "error": report.get("error"),
                       "analyst_turns": sum(1 for c in calls if c.get("role") == "analyst"),
                       "digester_actions_by_type": dict(dig_actions)},
           "transfer": {s: {k: v["S"] for k, v in rep["splits"][s].items()} for s in rep["splits"]},
           "final_files": sorted(res.best.files), "cache_hits_misses": [llm.hits, llm.misses],
           "wall_s": round(time.time() - t0, 1), "out_dir": str(out_dir)}
    save("live_agentic_smoke" + ("_replay" if a.cache_only else ""), out)
    print(json.dumps({k: out[k] for k in ("stop_reason", "proposer", "analyst", "transfer", "cache_hits_misses")},
                     indent=1))
    print("usage", res.usage.get("_total"))


if __name__ == "__main__":
    main()
