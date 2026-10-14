"""Re-score recorded demo runs with fresh trials, independent of everything the run itself sampled.

A run's loop and its shadow monitor score with trial seeds 0, 1, ... and the LLM cache is keyed on the seed, so a
post-run report on seed 0 just replays the very samples the loop selected its winner on (and the monitor's single
reading). This re-scores the seed and the final harness of each run on every split with trial seeds 100 and 101,
which no loop or monitor used, and writes ``<run>/rescore.json``::

    python experiments/demo/rescore.py                     # check: replay every recorded re-score offline at $0
    python experiments/demo/rescore.py metaharness_hard    # check one run (its directory under demo/runs)
    python experiments/demo/rescore.py --force rrsi        # record a new re-score (live calls; replaces rescore.json)

By default nothing is written and no model is called: each recorded re-score is replayed from the run's cache with the
model switched off (a miss is an error) and compared with ``rescore.json``; per-task scores go to
``rescore_check.json``. ``--force`` records a new one; the original ``started_at`` and ``usd`` are what
``spend.py`` attributes the cache entries by, so they are kept in ``rescore_history``. Runs recorded from now on get
the same re-score from ``live_demo._finish``.
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

from live_demo import OUT, RESCORE_K, RESCORE_SEED_OFFSET, _agentqa, rescore  # noqa: E402
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


def rescore_run(run: str, force: bool = False) -> dict:
    d = OUT / run
    summ = json.loads((d / "summary.json").read_text())
    suite = (summ.get("setup") or {}).get("suite") or "easy"
    prev = json.loads((d / "rescore.json").read_text()) if (d / "rescore.json").exists() else None
    live = force or prev is None
    llm = CachedLLM(ClaudeCLI("haiku", timeout_s=300), OUT / RUNS[run], offline=not live)
    t0 = time.time()
    arms = {"seed": Artifact(summ["seed_files"]), "final": Artifact(summ["final_files"])}
    rep = rescore(_agentqa(suite), llm, arms)
    # the run's own record is judged by its summary; here only the re-score's trials count
    errs = [f"re-score {arm} {split}: {v['missing']} trials missing"
            for split, row in rep["splits"].items() for arm, v in row.items() if (v.get("missing") or 0) > 0]
    scores = {s: {a: v["S"] for a, v in row.items()} for s, row in rep["splits"].items()}
    if not live:
        want = {s: {a: v["S"] for a, v in row.items()} for s, row in prev["transfer"]["splits"].items()}
        check = {"run": run, "matches": scores == want and not errs and llm.misses == 0, "cache_misses": llm.misses,
                 "scores": scores, "per_task": {s: {a: v.get("tasks") for a, v in row.items()}
                                                for s, row in rep["splits"].items()}}
        (d / "rescore_check.json").write_text(json.dumps(check, indent=1, default=str))
        print(run, "replay", "matches" if check["matches"] else "DIFFERS", "misses", llm.misses, flush=True)
        return {"clean": check["matches"]}
    out = {"run": run, "suite": suite, "k": RESCORE_K, "seed_offset": RESCORE_SEED_OFFSET,
           "started_at": t0, "wall_s": round(time.time() - t0, 1), "transfer": rep,
           "usd": round(llm.meter.total().cost_usd, 4), "cache": {"hits": llm.hits, "misses": llm.misses},
           "infra_errors": errs, "clean": not errs,
           "rescore_history": ((prev or {}).get("rescore_history") or []) + ([{k: prev[k] for k in (
               "started_at", "wall_s", "usd", "cache")}] if prev else [])}
    (d / "rescore.json").write_text(json.dumps(out, indent=1, default=str))
    print(run, json.dumps({s: {a: round(v, 3) for a, v in row.items()} for s, row in scores.items()}),
          "usd", out["usd"], "cache", out["cache"], flush=True)
    if errs:
        print("INFRA_ERRORS", len(errs), *errs[:5], sep="\n  ", flush=True)
    return out


if __name__ == "__main__":
    args = sys.argv[1:]
    force = "--force" in args
    todo = [a for a in args if a != "--force"] or list(RUNS)
    for run in todo:
        if run not in RUNS:
            raise SystemExit(f"unknown run {run!r}; choose from {list(RUNS)}")
    for run in todo:
        if not rescore_run(run, force)["clean"]:
            raise SystemExit(f"{run}: the replay differs or trials were lost; see its rescore_check.json / log")
