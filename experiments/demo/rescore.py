"""Re-score recorded demo runs with fresh trials, independent of everything the run itself sampled.

Shadow monitors and Meta-Harness's loop score with trial seeds 0, 1, ... (RRSI's loop uses hashed per-job seeds), and
the LLM cache is keyed on the seed, so a post-run report on seed 0 replays samples the run already drew (for
Meta-Harness, the very samples the loop selected its winner on). This re-scores the seed and the final harness of
each run on every split with trial seeds 100 and 101, which no loop decision or monitor reading used::

    python experiments/demo/rescore.py                       # check: replay every recorded re-score offline at $0
    python experiments/demo/rescore.py metaharness_hard      # check one run's (its directory under demo/runs)
    python experiments/demo/rescore.py --seed-offset 200     # check only the recorded rescore_s200.json files
    python experiments/demo/rescore.py --record --seed-offset 200 rrsi   # new samples: rescore_s200.json (live calls)

A check replays a recorded re-score (``rescore.json``, trial seeds 100 and 101, or ``rescore_s<N>.json``, seeds N and
N + 1) from the run's cache with the model switched off (a miss is an error), compares every score, writes the
per-task scores to ``<record>_check.json`` and exits 1 on any difference. Checks never call the model. Only
``--record`` does, and only for the runs it names and an N that gives trial seeds no evaluation of the run used
(``live_demo.new_seed_problem``). A recorded re-score is never replaced; ``spend.py`` attributes the cache entries
a ``--record`` writes by its cache-write journal, ``<run>/rescore_s<N>.cache_writes.txt``. Runs recorded from now on
carry the seed-100 re-score in their ``summary.json`` (``live_demo._finish``).
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

from live_demo import (JOURNAL, NEW_SEED_RECORD, OUT, RESCORE_K, RESCORE_SEED_OFFSET, _agentqa,  # noqa: E402
                       drop_unwritten_journal_lines, new_seed_problem, rescore)
from rsi.core import Artifact, CachedLLM, ClaudeCLI  # noqa: E402


def records(run: str) -> list[int]:
    """The seed offsets of a run's recorded re-scores: 100 for ``rescore.json``, N for each ``rescore_s<N>.json``."""
    d = OUT / run
    found = [RESCORE_SEED_OFFSET] if (d / "rescore.json").exists() else []
    found += [int(m.group(2)) for p in d.glob("rescore_s*.json") if (m := NEW_SEED_RECORD.fullmatch(p.name))]
    return sorted(found)


def recorded_runs() -> list[str]:
    """Every run directory under demo/runs with a recorded re-score."""
    dirs = {p.parent for p in [*OUT.glob("*/rescore*.json"), *OUT.glob("_archive/*/rescore*.json")]}
    return sorted(r for r in (str(d.relative_to(OUT)) for d in dirs) if records(r))


def cache_of(run: str) -> Path:
    """A run's LLM cache: ``.cache_<run>`` next to the run directory (live_demo names them so)."""
    return OUT / Path(run).parent / f".cache_{Path(run).name}"


def rescore_run(run: str, seed_offset: int = RESCORE_SEED_OFFSET, record: bool = False) -> dict:
    """Check the run's recorded re-score with ``seed_offset`` offline, or (``record``) record a new one live."""
    d = OUT / run
    summ = json.loads((d / "summary.json").read_text())
    suite = (summ.get("setup") or {}).get("suite") or "easy"
    rec = d / ("rescore.json" if seed_offset == RESCORE_SEED_OFFSET else f"rescore_s{seed_offset}.json")
    live = record
    if live and rec.exists():
        raise SystemExit(f"{rec} exists: a recorded re-score is never replaced")
    if live and (why := new_seed_problem(d, rec.name, seed_offset)):
        raise SystemExit(why)
    if not live and not rec.exists():
        raise SystemExit(f"{rec} does not exist (a check never calls the model; --record makes a new one)")
    if not cache_of(run).is_dir():
        raise SystemExit(f"{cache_of(run)} does not exist")
    if live:
        drop_unwritten_journal_lines()   # leftovers of a killed writer must not claim entries this one pays for
    llm = CachedLLM(ClaudeCLI("haiku", timeout_s=300), cache_of(run), offline=not live,
                    journal=d / f"{rec.stem}.{JOURNAL}" if live else None)
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
    if record:   # live calls: only for the runs named, with new trial seeds
        if offset is None or not args:
            raise SystemExit("--record needs --seed-offset N and the runs to re-score, e.g. --record --seed-offset 200 rrsi")
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
