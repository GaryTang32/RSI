"""Re-score recorded demo runs with fresh trials, independent of everything the run itself sampled.

A run's loop and its shadow monitor score with trial seeds 0, 1, ... and the LLM cache is keyed on the seed, so a
post-run report on seed 0 just replays the very samples the loop selected its winner on (and the monitor's single
reading). This re-scores the seed and the final harness of each run on every split with trial seeds 100 and 101,
which no loop or monitor used, and writes ``<run>/rescore.json``::

    python experiments/demo/rescore.py                     # every reported run and the replicates
    python experiments/demo/rescore.py metaharness_hard    # one run (its directory under demo/runs)

Each run's own cache is used, so a rerun replays at $0. Runs recorded from now on get the same re-score from
``live_demo._finish``.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT, ROOT / "experiments" / "demo"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from live_demo import OUT, RESCORE_K, RESCORE_SEED_OFFSET, _agentqa, infra_errors, rescore  # noqa: E402
from rsi.core import Artifact, CachedLLM, ClaudeCLI  # noqa: E402

#: run directory -> its LLM cache (both under demo/runs)
RUNS = {
    "metaharness": ".cache_metaharness",
    "metaharness_hard": ".cache_metaharness_hard",
    "rrsi": ".cache_rrsi",
    "rrsi_hard": ".cache_rrsi_hard",
    "_archive/metaharness_before_fix": "_archive/.cache_metaharness_before_fix",
    "_archive/metaharness_fix_v1": "_archive/.cache_metaharness_fix_v1",
    "_archive/metaharness_fix_v2": "_archive/.cache_metaharness_fix_v2",
}


def rescore_run(run: str) -> dict:
    d = OUT / run
    summ = json.loads((d / "summary.json").read_text())
    suite = (summ.get("setup") or {}).get("suite") or "easy"
    llm = CachedLLM(ClaudeCLI("haiku", timeout_s=300), OUT / RUNS[run])
    t0 = time.time()
    arms = {"seed": Artifact(summ["seed_files"]), "final": Artifact(summ["final_files"])}
    rep = rescore(_agentqa(suite), llm, arms)
    errs = infra_errors(d, rep) if (d / "trace.jsonl").exists() else []
    errs = [e for e in errs if e.startswith("re-score")]   # the run's own record is judged by its summary
    out = {"run": run, "suite": suite, "k": RESCORE_K, "seed_offset": RESCORE_SEED_OFFSET,
           "started_at": t0, "wall_s": round(time.time() - t0, 1), "transfer": rep,
           "usd": round(llm.meter.total().cost_usd, 4), "cache": {"hits": llm.hits, "misses": llm.misses},
           "infra_errors": errs, "clean": not errs}
    (d / "rescore.json").write_text(json.dumps(out, indent=1, default=str))
    print(run, json.dumps({s: {a: round(v["S"], 3) for a, v in row.items()} for s, row in rep["splits"].items()}),
          "usd", out["usd"], "cache", out["cache"], flush=True)
    if errs:
        print("INFRA_ERRORS", len(errs), *errs[:5], sep="\n  ", flush=True)
    return out


if __name__ == "__main__":
    todo = sys.argv[1:] or list(RUNS)
    for run in todo:
        if run not in RUNS:
            raise SystemExit(f"unknown run {run!r}; choose from {list(RUNS)}")
    for run in todo:
        if not rescore_run(run)["clean"]:
            raise SystemExit(f"{run}: trials lost to infrastructure errors; rerun after the cause is fixed")
