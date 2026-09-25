"""Live smoke run of the gene loop with a real model (claude -p, cached).

Katas domain, ``safe`` mode: Claude (default haiku) is both the frozen solver and the gene writer. The agent runs
``--cycles`` cycles on the evolve katas (look locally -> solve -> propose a gene from the public trace on failure
-> solidify with a discriminative validation + the graded task -> distil), then the library is evaluated against
no genes on the held-out katas (k=1). Cost is bounded by ``--max-usd`` (rsi.core.Budget) and the disk cache
(.rsi_cache/evomap) makes re-runs free.

Run: python experiments/evomap/live_smoke.py [--llm claude:haiku] [--cycles 8] [--max-usd 1.0]
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from _common import SAFE_HINT, cached_llm, save  # noqa: E402

from rsi.core import Budget  # noqa: E402
from rsi.domains.katas import KatasDomain, seed_harness  # noqa: E402
from rsi.evomap import Config, evaluate_library, read_library, render_gene, run  # noqa: E402


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
    cfg = Config(cycles=args.cycles, mode="safe", seed=0,
                 validation_hint=SAFE_HINT + ". The workspace contains only solution.py and smoke_test.py. Allowed "
                 "commands: `python <existing_script>.py` or `pytest -q <existing_file>.py`; inline code "
                 "(python -c) and shell syntax are BLOCKED by the command policy")
    res = run(dom, seed_harness(), llm_task=llm, llm_propose=llm, config=cfg,
              out_dir=ROOT / "results" / "evomap" / "live_smoke_run", budget=Budget(max_rounds=args.cycles,
                                                                                     max_usd=args.max_usd))
    loop_s = time.time() - t0
    rep = evaluate_library(dom, llm, res, splits=("holdout",), k=1, workers=4)
    genes = read_library(res.best)
    usage = {**llm.meter.snapshot()}
    out = {"config": {"llm": args.llm, "cycles": args.cycles, "mode": "safe", "max_usd": args.max_usd},
           "trajectory": [{k: r[k] for k in ("cycle", "task_id", "source", "gene_id", "task_score", "solidified",
                                             "composite", "validation_ok")} for r in res.trajectory],
           "genes": [render_gene(g) for g in genes], "gene_validations": {g.id: g.validation for g in genes},
           "audit": res.meta["audit"], "stop_reason": res.stop_reason,
           "holdout": {a: rep["splits"]["holdout"][a]["S"] for a in ("no_genes", "gene_library")},
           "holdout_paired": rep["splits"]["holdout"]["gene_library"]["vs_reference"],
           "usage": usage, "wall_s": round(time.time() - t0, 1), "loop_wall_s": round(loop_s, 1)}
    save("live_smoke", out, args.out)
    for r in out["trajectory"]:
        print(r)
    print("genes:", [g.id for g in genes])
    print("holdout:", out["holdout"], "usd:", usage.get("_total", {}).get("cost_usd"))


if __name__ == "__main__":
    main()
