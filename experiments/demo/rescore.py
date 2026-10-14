"""Re-score recorded demo runs with fresh trials, independent of everything the run itself sampled.

Shadow monitors and Meta-Harness's loop score with trial seeds 0, 1, ... (RRSI's loop uses hashed per-job seeds), and
the LLM cache is keyed on the seed, so a post-run report on seed 0 replays samples the run already drew (for
Meta-Harness, the very samples the loop selected its winner on). This re-scores the seed and the final harness of
each run on every split with trial seeds 100 and 101, which no loop decision or monitor reading used::

    python experiments/demo/rescore.py                          # check: replay every recorded re-score offline at $0
    python experiments/demo/rescore.py metaharness_hard         # check one run (its directory under demo/runs)
    python experiments/demo/rescore.py --seed-offset 200 rrsi   # new samples: records rescore_s200.json (live calls)

The check replays a run's recorded ``rescore.json`` from its cache with the model switched off (a miss is an error),
compares every score and writes the per-task scores to ``rescore_check.json``. ``--seed-offset N`` checks
``rescore_s<N>.json`` the same way when it exists and records it when it does not; N must give trial seeds no
evaluation of the run used (``live_demo.new_seed_problem``). A recorded re-score is never replaced: ``spend.py``
attributes the run's cache entries by its ``started_at``. Runs recorded from now on carry the same re-score in their
``summary.json`` (``live_demo._finish``).
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

from live_demo import OUT, RESCORE_K, RESCORE_SEED_OFFSET, _agentqa, new_seed_problem, rescore  # noqa: E402
from rsi.core import Artifact, CachedLLM, ClaudeCLI  # noqa: E402


def recorded_runs() -> list[str]:
    """Every run directory under demo/runs with a recorded re-score."""
    found = [*OUT.glob("*/rescore.json"), *OUT.glob("_archive/*/rescore.json")]
    return sorted(str(p.parent.relative_to(OUT)) for p in found)


def cache_of(run: str) -> Path:
    """A run's LLM cache: ``.cache_<run>`` next to the run directory (live_demo names them so)."""
    return OUT / Path(run).parent / f".cache_{Path(run).name}"


def rescore_run(run: str, seed_offset: int = RESCORE_SEED_OFFSET) -> dict:
    d = OUT / run
    summ = json.loads((d / "summary.json").read_text())
    suite = (summ.get("setup") or {}).get("suite") or "easy"
    rec = d / ("rescore.json" if seed_offset == RESCORE_SEED_OFFSET else f"rescore_s{seed_offset}.json")
    live = not rec.exists()
    if live and seed_offset == RESCORE_SEED_OFFSET:
        raise SystemExit(f"{rec} does not exist: the recorded re-score is the reference (use --seed-offset N for new "
                         f"samples)")
    if seed_offset != RESCORE_SEED_OFFSET and (why := new_seed_problem(d, rec.name, seed_offset)):
        raise SystemExit(why)
    if not cache_of(run).is_dir():
        raise SystemExit(f"{cache_of(run)} does not exist")
    llm = CachedLLM(ClaudeCLI("haiku", timeout_s=300), cache_of(run), offline=not live)
    t0 = time.time()
    arms = {"seed": Artifact(summ["seed_files"]), "final": Artifact(summ["final_files"])}
    rep = rescore(_agentqa(suite), llm, arms, seed_offset=seed_offset)
    errs = [f"re-score {arm} {split}: {v['missing']} trials missing"
            for split, row in rep["splits"].items() for arm, v in row.items() if (v.get("missing") or 0) > 0]
    scores = {s: {a: v["S"] for a, v in row.items()} for s, row in rep["splits"].items()}
    if not live:
        want = {s: {a: v["S"] for a, v in row.items()} for s, row in json.loads(rec.read_text())["transfer"]["splits"].items()}
        check = {"run": run, "record": rec.name, "matches": scores == want and not errs and llm.misses == 0,
                 "cache_misses": llm.misses, "scores": scores,
                 "per_task": {s: {a: v.get("tasks") for a, v in row.items()} for s, row in rep["splits"].items()}}
        (d / rec.name.replace(".json", "_check.json")).write_text(json.dumps(check, indent=1, default=str))
        print(run, rec.name, "replay", "matches" if check["matches"] else "DIFFERS", "misses", llm.misses, flush=True)
        return {"clean": check["matches"]}
    out = {"run": run, "suite": suite, "k": RESCORE_K, "seed_offset": seed_offset,
           "started_at": t0, "wall_s": round(time.time() - t0, 1), "transfer": rep,
           "usd": round(llm.meter.total().cost_usd, 4), "cache": {"hits": llm.hits, "misses": llm.misses},
           "infra_errors": errs, "clean": not errs}
    rec.write_text(json.dumps(out, indent=1, default=str))
    print(run, json.dumps({s: {a: round(v, 3) for a, v in row.items()} for s, row in scores.items()}),
          "usd", out["usd"], "cache", out["cache"], flush=True)
    if llm.hits:   # e.g. a final harness identical to the seed repeats its prompts; anything else replayed a sample
        print(f"note: {llm.hits} calls answered from the cache", flush=True)
    if errs:
        print("INFRA_ERRORS", len(errs), *errs[:5], sep="\n  ", flush=True)
    return out


if __name__ == "__main__":
    args = sys.argv[1:]
    offset = RESCORE_SEED_OFFSET
    if "--seed-offset" in args:
        i = args.index("--seed-offset")
        offset = int(args[i + 1])
        del args[i:i + 2]
    todo = args or recorded_runs()
    for run in todo:
        if not (OUT / run / "summary.json").exists():
            raise SystemExit(f"{OUT / run} has no summary.json; runs with a recorded re-score: {recorded_runs()}")
    for run in todo:
        if not rescore_run(run, offset)["clean"]:
            raise SystemExit(f"{run}: the replay differs or trials were lost; see its *_check.json / log")
