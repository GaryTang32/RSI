"""Live smoke (P10, claims M4, M28): the json_actions proposer and the agentic analyst end to end with Claude Haiku.

Run: python experiments/rrsi/live_agentic_smoke.py --cache-dir DIR [--max-usd 1.0] [--cache-only]
"""
import argparse
import collections
import json
import time
from pathlib import Path

from _common import save
from live_smoke import _RefuseCalls

from rsi.core import Budget, CachedLLM, ClaudeCLI
from rsi.domains.agentqa import AgentQADomain, SimModel, make_suite
from rsi.rrsi import Config, paired_transfer, run


def action_counts(replies: list) -> dict:
    """JSON-action replies counted by their "action" field."""
    counts = collections.Counter()
    for reply in replies:
        try:
            counts[json.loads(reply or "{}").get("action", "?")] += 1
        except (json.JSONDecodeError, AttributeError):
            counts["(unparseable)"] += 1
    return dict(counts)


def read_json(p: Path) -> dict:
    return json.loads(p.read_text()) if p.exists() else {}


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
    tp = out_dir / "trace.jsonl"
    trace = [dict(e.get("data") or {}, kind=e.get("kind"))
             for e in map(json.loads, filter(str.strip, tp.read_text().splitlines()))] if tp.exists() else []
    an = [ev for ev in trace if ev.get("kind") == "analysis"]
    calls = an[0].get("llm_calls", []) if an else []
    report = read_json(out_dir / "r0" / "analysis_report.json")
    prop = read_json(out_dir / "r0" / "A" / "proposal.json")
    out = {"experiment": "live smoke of the agentic protocols (retry round 2: P10)", "model": a.model,
           "config": cfg.dump(), "stop_reason": res.stop_reason, "trajectory": res.trajectory,
           "usage": res.usage, "spend": res.meta.get("spend"),
           "proposer": {"status": prop.get("status"), "n_edits": prop.get("n_edits"),
                        "edits": [{k: e.get(k) for k in ("id", "component", "hypothesis")}
                                  for e in prop.get("edits", [])],
                        "json_actions_by_type": action_counts([ev.get("reply") for ev in trace
                                                               if ev.get("kind") == "proposal"])},
           "analyst": {"n_digests": report.get("n_digests"),
                       "failure_modes": [m.get("mode") for m in report.get("failure_modes", [])],
                       "error": report.get("error"),
                       "analyst_turns": sum(1 for c in calls if c.get("role") == "analyst"),
                       "digester_actions_by_type": action_counts([c.get("reply") for c in calls
                                                                  if c.get("role") == "digester"])},
           "transfer": {s: {k: v["S"] for k, v in rep["splits"][s].items()} for s in rep["splits"]},
           "final_files": sorted(res.best.files), "cache_hits_misses": [llm.hits, llm.misses],
           "wall_s": round(time.time() - t0, 1), "out_dir": str(out_dir)}
    save("live_agentic_smoke" + ("_replay" if a.cache_only else ""), out)
    print(json.dumps({k: out[k] for k in ("stop_reason", "proposer", "analyst", "transfer", "cache_hits_misses")},
                     indent=1))
    print("usage", res.usage.get("_total"))


if __name__ == "__main__":
    main()
