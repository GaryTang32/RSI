"""Re-score recorded demo runs with fresh trial seeds: check every recorded re-score offline, or record a new one.

    python experiments/demo/rescore.py [RUN ...] [--seed-offset N] [--record]   # --record needs N and RUN (live)
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from _demo_common import per_arm
from live_demo import (JOURNAL, OUT, RESCORE_K, RESCORE_SEED_OFFSET, _agentqa, begin_live_write, new_seed_problem,
                       record_offsets, record_path, rescore)

from rsi.core import Artifact, CachedLLM, ClaudeCLI


def records(run: str) -> list[int]:
    return record_offsets(OUT / run, "rescore")


def recorded_runs() -> list[str]:
    dirs = {p.parent for p in [*OUT.glob("*/rescore*.json"), *OUT.glob("_archive/*/rescore*.json")]}
    return sorted(r for r in (str(d.relative_to(OUT)) for d in dirs) if records(r))


def cache_of(run: str) -> Path:
    return OUT / Path(run).parent / f".cache_{Path(run).name}"


def rescore_run(run: str, seed_offset: int = RESCORE_SEED_OFFSET, record: bool = False) -> dict:
    """Check the run's recorded re-score at ``seed_offset`` offline, or (``record``) record a new one live."""
    d = OUT / run
    summ = json.loads((d / "summary.json").read_text())
    suite = (summ.get("setup") or {}).get("suite") or "easy"
    rec = record_path(d, "rescore", seed_offset)
    if record and rec.exists():
        raise SystemExit(f"{rec} exists: a recorded re-score is never replaced")
    if record and (why := new_seed_problem(d, rec.name, seed_offset)):
        raise SystemExit(why)
    if not record and not rec.exists():
        raise SystemExit(f"{rec} does not exist (a check never calls the model; --record makes a new one)")
    if not cache_of(run).is_dir():
        raise SystemExit(f"{cache_of(run)} does not exist")
    if record:
        begin_live_write()
    llm = CachedLLM(ClaudeCLI("haiku", timeout_s=300), cache_of(run), offline=not record,
                    journal=d / f"{rec.stem}.{JOURNAL}" if record else None)
    t0 = time.time()
    arms = {"seed": Artifact(summ["seed_files"]), "final": Artifact(summ["final_files"])}
    rep = rescore(_agentqa(suite), llm, arms, seed_offset=seed_offset)
    errs = [f"re-score {arm} {split}: {v['missing']} trials missing"
            for split, row in rep["splits"].items() for arm, v in row.items() if (v.get("missing") or 0) > 0]
    scores = per_arm(rep)
    if not record:
        want = per_arm(json.loads(rec.read_text())["transfer"])
        check = {"run": run, "record": rec.name, "matches": scores == want and not errs and llm.misses == 0,
                 "cache_misses": llm.misses, "scores": scores, "per_task": per_arm(rep, "tasks")}
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


def main(args: list[str]) -> None:
    record = "--record" in args
    args = [a for a in args if a != "--record"]
    offset = None
    if "--seed-offset" in args:
        i = args.index("--seed-offset")
        offset = int(args[i + 1])
        del args[i:i + 2]
    for run in args:
        if not (OUT / run / "summary.json").exists():
            raise SystemExit(f"{OUT / run} has no summary.json; runs with a recorded re-score: {recorded_runs()}")
    if record:
        if offset is None or not args:
            raise SystemExit("--record needs --seed-offset N and the runs to re-score, "
                             "e.g. --record --seed-offset 200 rrsi")
        jobs = [(run, offset) for run in args]
    else:
        want = "" if offset is None else f" with --seed-offset {offset}"
        jobs = [(run, o) for run in (args or recorded_runs()) for o in records(run) if offset is None or o == offset]
        missing = [run for run in args if all(r != run for r, _ in jobs)]
        if missing or not jobs:
            raise SystemExit(f"no recorded re-score{want} to check{' for ' + ', '.join(missing) if missing else ''}")
    for run, o in jobs:
        if not rescore_run(run, o, record)["clean"]:
            raise SystemExit(f"{run}: the replay differs or trials were lost; see its *_check.json / log")


if __name__ == "__main__":
    main(sys.argv[1:])
