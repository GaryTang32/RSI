"""Live smoke run: RRSI end-to-end with a real LLM in the search roles.

AgentQA harness evolution with ``claude -p`` (default haiku) as proposer, leakage critic and
analyst/digesters, SimModel as the frozen task model (cheap, deterministic), all LLM calls
cached under ``.rsi_cache/rrsi`` so a re-run replays for free. Small by design (T = 2,
m = 2): it checks that the real model follows the done() contract, that its diffs are
tagged, screened, smoke-tested, evaluated and adjudicated, and reports per-role usage and
cost. ``--llm sim`` runs the same pipeline with the scripted mock.

    python experiments/rrsi/live_smoke.py --llm claude:haiku
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import parse_args, save, search_llm  # noqa: E402

from rsi.core import Budget  # noqa: E402
from rsi.domains.agentqa import AgentQADomain, SimModel, make_suite  # noqa: E402
from rsi.rrsi import Config, paired_transfer, run  # noqa: E402


def main():
    a = parse_args("live smoke run of RRSI", default_seeds=1,
                   extra=lambda ap: (ap.add_argument("--T", type=int, default=2),
                                     ap.add_argument("--max-usd", type=float, default=1.0)))
    suite = make_suite(n_evolve=12, n_holdout=12, n_ood_per_family=3, seed=0)
    dom = AgentQADomain(suite)
    llm = search_llm(a.llm, agentqa=True)
    cfg = Config(T=a.T, k=2, workers=4, analyst="llm" if a.llm != "sim" else "heuristic", max_digests=3,
                 n_fail_traces=6, n_success_traces=2, max_done_bounces=2, repair_rounds=2)
    out_dir = Path(tempfile.mkdtemp(prefix="rrsi_live_"))
    res = run(dom, AgentQADomain.seed_artifact(), llm_task=SimModel(suite), llm_propose=llm, config=cfg,
              out_dir=out_dir, budget=Budget(max_usd=a.max_usd, max_wall_s=900), verbose=True)
    rep = paired_transfer(dom, SimModel(suite), {"H0": res.baseline, "final": res.best}, k=2)
    props = []
    for p in sorted(out_dir.glob("r*/*/proposal*.json")):
        d = json.loads(p.read_text())
        props.append({"file": str(p.relative_to(out_dir)), "status": d.get("status"), "n_changes": d.get("n_changes"),
                      "edits": [{k: e.get(k) for k in ("id", "component", "hypothesis")} for e in d.get("edits", [])],
                      "bounces": len(d.get("log", [])) - 1})
    crit = {str(p.relative_to(out_dir)): json.loads(p.read_text()) for p in sorted(out_dir.glob("r*/*/critic.json"))}
    diffs = {str(p.relative_to(out_dir)): p.read_text()[:3000] for p in sorted(out_dir.glob("r*/*/diff.patch"))}
    out = {"experiment": "live smoke", "llm": a.llm, "task_model": "SimModel", "config": cfg.dump(),
           "stop_reason": res.stop_reason, "trajectory": res.trajectory, "usage": res.usage,
           "transfer": {s: {k: v["S"] for k, v in rep["splits"][s].items()} for s in rep["splits"]},
           "proposals": props, "critic": crit, "diffs": diffs, "final_files": sorted(res.best.files),
           "delta": res.meta.get("delta"), "out_dir": str(out_dir)}
    save("live_smoke" if a.llm != "sim" else "live_smoke_sim", out)
    print(json.dumps({"stop": res.stop_reason, "usage": res.usage.get("_total"), "transfer": out["transfer"],
                      "proposals": props}, indent=1))


if __name__ == "__main__":
    main()
