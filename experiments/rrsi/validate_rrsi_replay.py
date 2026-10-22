"""$0 replay of the recorded live validation run on the current code (offline cache; a miss is an error, not a call).

Run: python experiments/rrsi/validate_rrsi_replay.py [--out DIR]
"""
import argparse
import json
import tempfile
from pathlib import Path

from _common import ROOT

from rsi.core import CachedLLM, ClaudeCLI
from rsi.rrsi import Config, run

REC = ROOT / "validation" / "rrsi" / "live_agentqa"
CACHE = ROOT / "validation" / "rrsi" / ".cache_live_agentqa"


def _hist(p: Path) -> list:
    return [(r["t"], r["variant"], r.get("edit_id"), r.get("component"), r["outcome"], r.get("S"), r.get("delta_S"),
             r.get("delta_C")) for r in map(json.loads, p.read_text().splitlines()) if r.get("edit_id")]


def _json(p: Path):
    return json.loads(p.read_text())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    from rsi.domains.agentqa import AgentQADomain, make_suite
    out = Path(a.out or tempfile.mkdtemp(prefix="rrsi_live_replay_"))
    rec_fr = _json(REC / "frontier.json")
    settled = len(rec_fr["trajectory"]) - 1
    suite = make_suite(n_evolve=12, n_holdout=12, n_ood_per_family=3, seed=0)
    dom = AgentQADomain(suite)
    llm = CachedLLM(ClaudeCLI("haiku", timeout_s=400), CACHE, offline=True)
    task = CachedLLM(ClaudeCLI("haiku", timeout_s=200), CACHE, offline=True)
    cfg = Config(T=4, m=2, k=1, workers=4, seed=0, analyst="llm", calibration_repeats=3, max_digests=4,
                 n_fail_traces=6, n_success_traces=3, repair_rounds=2, max_done_bounces=2)

    def hook(event, **info):
        """Stop after the rounds the recorded run settled."""
        if event == "settled" and info.get("t", -1) + 1 >= settled:
            (out / "STOP").write_text("replay: recorded run settled only these rounds\n")

    res = run(dom, AgentQADomain.seed_artifact(), llm_task=task, llm_propose=llm, llm_analyst=llm, config=cfg,
              out_dir=out, hooks={"settled": hook})
    fr = _json(out / "frontier.json")
    same = {
        "history": _hist(out / "history.jsonl") == _hist(REC / "history.jsonl"),
        "trajectory": [x["S"] for x in fr["trajectory"]] == [x["S"] for x in rec_fr["trajectory"]],
        "final_incumbent": fr["incumbent"]["artifact_id"] == rec_fr["incumbent"]["artifact_id"],
        "decisions": all(_json(out / f"r{t}" / "decisions.json") == _json(REC / f"r{t}" / "decisions.json")
                         for t in range(settled)),
        "calibration_delta": _json(out / "calibration.json")["delta"] == _json(REC / "calibration.json")["delta"],
    }
    rep = {"out": str(out), "settled_rounds": len(fr["trajectory"]) - 1, "identical": same,
           "cache": {"search_hits_misses": [llm.hits, llm.misses], "task_hits_misses": [task.hits, task.misses]},
           "metered_usd": res.usage.get("_total", {}).get("cost_usd", 0.0), "stop_reason": res.stop_reason}
    (REC / "replay_current_code.json").write_text(json.dumps(rep, indent=1))
    print(json.dumps(rep, indent=1))


if __name__ == "__main__":
    main()
