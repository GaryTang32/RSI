"""Live smoke: RRSI end to end on AgentQA with a real LLM as proposer, critic and analyst (SimModel solves the tasks).

Run: python experiments/rrsi/live_smoke.py --llm claude:haiku|sim [--cache-dir DIR] [--cache-only]
"""
import json
import tempfile
from pathlib import Path

from _common import RESULTS, parse_args, save, search_llm

from rsi.core import LLM, Budget, CachedLLM, LLMResponse, Usage
from rsi.domains.agentqa import AgentQADomain, SimModel, make_suite
from rsi.rrsi import Config, paired_transfer, run


class _RefuseCalls(LLM):
    """Live backend stand-in for a cache-only replay: every call is refused (no network, no cost)."""

    def __init__(self, name: str) -> None:
        super().__init__()
        self.name = name
        self.refused = 0

    def _complete(self, prompt, *, system, max_tokens, seed):
        self.refused += 1
        return LLMResponse(text="", usage=Usage(), model=self.name, error="cache miss refused (--cache-only)")


def proposals(out_dir: Path) -> list[dict]:
    props = []
    for p in sorted(out_dir.glob("r*/*/proposal*.json")):
        d = json.loads(p.read_text())
        props.append({"file": str(p.relative_to(out_dir)), "status": d.get("status"), "n_changes": d.get("n_changes"),
                      "edits": [{k: e.get(k) for k in ("id", "component", "hypothesis")} for e in d.get("edits", [])],
                      "bounces": len(d.get("log", [])) - 1})
    return props


def replay_report(llm, res, out: dict) -> dict:
    """--cache-only: cache use and whether the replay matches the recorded live run (results/rrsi/live_smoke.json)."""
    rec_p = RESULTS / "live_smoke.json"
    rec = json.loads(rec_p.read_text()) if rec_p.exists() else {}
    traj = rec.get("trajectory", [])
    return {"cache_hits": llm.hits, "cache_misses": llm.misses, "refused_calls": llm.inner.refused,
            "identical_to_recorded_live_run": {
                "trajectory": [r["S"] for r in res.trajectory] == [r["S"] for r in traj],
                "winners": [r.get("winner") for r in res.trajectory] == [r.get("winner") for r in traj],
                "transfer": out["transfer"] == rec.get("transfer"),
                "final_files": out["final_files"] == rec.get("final_files")}}


def main():
    a = parse_args("live smoke run of RRSI", default_seeds=1,
                   extra=lambda ap: (ap.add_argument("--T", type=int, default=2),
                                     ap.add_argument("--max-usd", type=float, default=1.0),
                                     ap.add_argument("--cache-dir", default=None,
                                                     help="LLM cache directory (default .rsi_cache/rrsi)"),
                                     ap.add_argument("--cache-only", action="store_true")))
    suite = make_suite(n_evolve=12, n_holdout=12, n_ood_per_family=3, seed=0)
    dom = AgentQADomain(suite)
    llm = search_llm(a.llm, agentqa=True, cache_dir=a.cache_dir)
    if a.cache_only:
        if not isinstance(llm, CachedLLM):
            raise SystemExit("--cache-only needs --llm claude:<model>")
        llm = CachedLLM(_RefuseCalls(llm.inner.name), llm.dir)
    cfg = Config(T=a.T, k=2, workers=4, analyst="llm" if a.llm != "sim" else "heuristic", max_digests=3,
                 n_fail_traces=6, n_success_traces=2, max_done_bounces=2, repair_rounds=2)
    out_dir = Path(tempfile.mkdtemp(prefix="rrsi_live_"))
    res = run(dom, AgentQADomain.seed_artifact(), llm_task=SimModel(suite), llm_propose=llm, config=cfg,
              out_dir=out_dir, budget=Budget(max_usd=a.max_usd, max_wall_s=900), verbose=True)
    rep = paired_transfer(dom, SimModel(suite), {"H0": res.baseline, "final": res.best}, k=2)
    props = proposals(out_dir)
    crit = {str(p.relative_to(out_dir)): json.loads(p.read_text()) for p in sorted(out_dir.glob("r*/*/critic.json"))}
    diffs = {str(p.relative_to(out_dir)): p.read_text()[:3000] for p in sorted(out_dir.glob("r*/*/diff.patch"))}
    out = {"experiment": "live smoke", "llm": a.llm, "task_model": "SimModel", "config": cfg.dump(),
           "stop_reason": res.stop_reason, "trajectory": res.trajectory, "usage": res.usage,
           "transfer": {s: {k: v["S"] for k, v in rep["splits"][s].items()} for s in rep["splits"]},
           "proposals": props, "critic": crit, "diffs": diffs, "final_files": sorted(res.best.files),
           "delta": res.meta.get("delta"), "out_dir": str(out_dir), "cache_dir": str(getattr(llm, "dir", "")),
           "spend": res.meta.get("spend"),
           "cache_hits_misses": [getattr(llm, "hits", None), getattr(llm, "misses", None)]}
    if a.cache_only:
        out["replay"] = replay_report(llm, res, out)
    save("live_smoke_replay" if a.cache_only else ("live_smoke" if a.llm != "sim" else "live_smoke_sim"), out)
    print(json.dumps({"stop": res.stop_reason, "usage": res.usage.get("_total"), "transfer": out["transfer"],
                      "proposals": props, "replay": out.get("replay")}, indent=1))


if __name__ == "__main__":
    main()
