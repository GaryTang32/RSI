"""Live smoke run of the gene loop with a real model (claude -p, cached).
Run: python experiments/evomap/live_smoke.py [--llm claude:haiku] [--cycles 8] [--max-usd 1.0]
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

from _common import KATA_SAFE_HINT, RESULTS, cached_llm, save

from rsi.core import Budget
from rsi.domains.katas import KatasDomain, seed_harness
from rsi.evomap import Config, evaluate_library, read_library, render_gene, run

TRAJECTORY_KEYS = ("cycle", "task_id", "source", "gene_id", "task_score", "solidified", "composite", "validation_ok")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--llm", default="claude:haiku")
    ap.add_argument("--cycles", type=int, default=8)
    ap.add_argument("--max-usd", type=float, default=1.0)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    llm = cached_llm(args.llm)
    dom = KatasDomain()
    t0 = time.time()
    cfg = Config(cycles=args.cycles, mode="safe", seed=0, validation_hint=KATA_SAFE_HINT)
    # a re-run resumes the store in the run directory, so a plumbing check with --out never touches the recorded run
    run_dir = Path(args.out).with_suffix("").parent / (Path(args.out).stem + "_run") if args.out else \
        RESULTS / "live_smoke_run"
    res = run(dom, seed_harness(), llm_task=llm, llm_propose=llm, config=cfg, out_dir=run_dir,
              budget=Budget(max_rounds=args.cycles, max_usd=args.max_usd))
    loop_s = time.time() - t0
    rep = evaluate_library(dom, llm, res, splits=("holdout",), k=1, workers=4)
    genes = read_library(res.best)
    usage = {**llm.meter.snapshot()}
    holdout = rep["splits"]["holdout"]
    out = {"config": {"llm": args.llm, "cycles": args.cycles, "mode": "safe", "max_usd": args.max_usd},
           "trajectory": [{k: r[k] for k in TRAJECTORY_KEYS} for r in res.trajectory],
           "genes": [render_gene(g) for g in genes], "gene_validations": {g.id: g.validation for g in genes},
           "audit": res.meta["audit"], "stop_reason": res.stop_reason,
           "holdout": {a: holdout[a]["S"] for a in ("no_genes", "gene_library")},
           "holdout_paired": holdout["gene_library"]["vs_reference"],
           "usage": usage, "wall_s": round(time.time() - t0, 1), "loop_wall_s": round(loop_s, 1)}
    save("live_smoke", out, args.out)
    for r in out["trajectory"]:
        print(r)
    print("genes:", [g.id for g in genes])
    print("holdout:", out["holdout"], "usd:", usage.get("_total", {}).get("cost_usd"))


if __name__ == "__main__":
    main()
