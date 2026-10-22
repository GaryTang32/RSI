"""Does text copied from practice traces into the hard Meta-Harness run's final harness drive its scores?

    python experiments/demo/leak_ablation.py [--seed-offset N] [--record]   # check records offline; --record is live
"""
from __future__ import annotations

import json
import re
import sys
import time

from _demo_common import RUNS, per_arm
from live_demo import (JOURNAL, RESCORE_K, _agentqa, begin_live_write, new_seed_problem, record_offsets, record_path,
                       rescore)

from rsi.core import Artifact, CachedLLM, ClaudeCLI

RUN = RUNS / "metaharness_hard"
#: replacements, applied in order; none of the new strings is any task's answer or exchange rate
NEUTRAL = {"$2,379.20": "$1,234.50", "2379.20": "1234.50", "EUR_RATE = 1.145": "EUR_RATE = 1.100",
           "`Harper`, `Delta`, `Cobalt`": "`Ingrid`, `Orion`, `Vesper`"}


def copied_strings(files: dict[str, str], dom) -> list[str]:
    """Every task answer (numbers also with thousands separators) and practice exchange rate found in ``files``."""
    text = "\n".join(files.values())
    found = []
    for split in ("evolve", "holdout", "ood"):
        for t in dom.tasks.split(split, allow_sealed=True):
            tg = str(t.target)
            forms = {tg}
            if re.fullmatch(r"\d+\.\d\d", tg):
                whole, cents = tg.split(".")
                forms.add(f"{int(whole):,}.{cents}")
            found += [f"{t.id} answer {f}" for f in sorted(forms)
                      if re.search(r"(?<![\w.])" + re.escape(f) + r"(?!\w)", text)]
            if split == "evolve":
                found += [f"{t.id} rate {r}" for r in re.findall(r"1 EUR = ([\d.]+) USD", t.input) if r in text]
    return found


def main() -> None:
    args = sys.argv[1:]
    record = "--record" in args
    offset = int(args[args.index("--seed-offset") + 1]) if "--seed-offset" in args else None
    if record and offset is None:
        raise SystemExit("--record needs --seed-offset N (new trial seeds)")
    todo = [offset] if record else [o for o in record_offsets(RUN, "leak_ablation")
                                    if offset is None or o == offset]
    if not todo:
        raise SystemExit(f"no recorded leak check{'' if offset is None else f' with --seed-offset {offset}'} to check")
    for o in todo:
        run(o, record)


def _neutralised(final: Artifact) -> tuple[Artifact, list[str]]:
    files, changed = dict(final.files), []
    for path, text in files.items():
        new = text
        for old, rep in NEUTRAL.items():
            new = new.replace(old, rep)
        if new != text:
            files[path] = new
            changed.append(path)
    return Artifact(files), changed


def run(offset: int, record: bool) -> None:
    """Check the recorded leak check at ``offset`` offline, or (``record``) record a new one live."""
    final = Artifact.from_dir(RUN / "final_harness")
    neutral, changed = _neutralised(final)
    dom = _agentqa("hard")
    before, left = copied_strings(final.files, dom), copied_strings(neutral.files, dom)
    if left:
        raise SystemExit(f"the neutral copy still contains copied strings: {left}")
    rec = record_path(RUN, "leak_ablation", offset)
    if record and rec.exists():
        raise SystemExit(f"{rec} exists: a recorded leak check is never replaced")
    if record and (why := new_seed_problem(RUN, rec.name, offset)):
        raise SystemExit(why)
    if record:
        begin_live_write()
    llm = CachedLLM(ClaudeCLI("haiku", timeout_s=300), RUNS / ".cache_metaharness_hard",
                    offline=not record, journal=RUN / f"{rec.stem}.{JOURNAL}" if record else None)
    t0 = time.time()
    rep = rescore(dom, llm, {"final": final, "final_neutralised": neutral}, seed_offset=offset)
    scores = per_arm(rep)
    if not record:
        check = {"matches": scores == json.loads(rec.read_text())["scores"] and llm.misses == 0,
                 "cache_misses": llm.misses, "scores": scores, "per_task": per_arm(rep, "tasks")}
        (RUN / rec.name.replace(".json", "_check.json")).write_text(json.dumps(check, indent=1, default=str))
        print(rec.name, "replay", "matches" if check["matches"] else "DIFFERS", "misses", llm.misses)
        if not check["matches"]:
            raise SystemExit(1)
        return
    missing = per_arm(rep, "missing")
    out = {"replaced": NEUTRAL, "files_changed": changed, "copied_in_final": before, "copied_in_neutral": left,
           "k": RESCORE_K, "seed_offset": offset, "started_at": t0, "scores": scores,
           "families": per_arm(rep, "families"),
           "paired_vs_final": {s: row["final_neutralised"].get("vs_reference") for s, row in rep["splits"].items()},
           "missing": missing, "clean": not any(n for row in missing.values() for n in row.values()),
           "usd": round(llm.meter.total().cost_usd, 4), "cache": {"hits": llm.hits, "misses": llm.misses}}
    rec.write_text(json.dumps(out, indent=1, default=str))
    print(json.dumps(out, indent=1, default=str))
    if not out["clean"]:
        print("INFRA_ERRORS: trials missing", missing)


if __name__ == "__main__":
    main()
