"""Does text copied from practice traces into a kept harness drive its scores?

The hard Meta-Harness run's final prompts use strings copied from practice traces as format examples. Three are
exact practice answers: ``2379.20`` (also written ``$2,379.20``; evolve-ledger-003), ``Harper`` (evolve-logic-000
and -004) and ``Delta`` (evolve-ledger-007). ``Delta`` is also the answer of one sealed held-out task
(holdout-ledger-001), because the customer pool is small. ``Cobalt`` is a customer named in every ledger question
but no task's answer, and the code template copies practice task evolve-ledger-005's exchange rate
(``EUR_RATE = 1.145``). This re-scores the final harness and a copy with every one of those strings replaced by a
neutral one, on all three splits, with the same fresh trial seeds the post-run re-score uses (``rescore.py``), and
first checks that the neutral copy contains no task's answer and no practice exchange rate::

    python experiments/demo/leak_ablation.py      # writes demo/runs/metaharness_hard/leak_ablation.json

If the scores hold, the copied strings did not drive the result; if they drop, they did.
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT, ROOT / "experiments" / "demo"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from live_demo import RESCORE_K, RESCORE_SEED_OFFSET, _agentqa, rescore  # noqa: E402
from rsi.core import Artifact, CachedLLM, ClaudeCLI  # noqa: E402

RUN = ROOT / "demo" / "runs" / "metaharness_hard"
#: replacements, applied in order; none of the new strings is any task's answer or exchange rate
NEUTRAL = {"$2,379.20": "$1,234.50", "2379.20": "1234.50", "EUR_RATE = 1.145": "EUR_RATE = 1.100",
           "`Harper`, `Delta`, `Cobalt`": "`Ingrid`, `Orion`, `Vesper`"}


def copied_strings(files: dict[str, str], dom) -> list[str]:
    """Every task answer (any split; numbers also with thousands separators) and every practice exchange rate that
    occurs in ``files``."""
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
    final = Artifact.from_dir(RUN / "final_harness")
    files = dict(final.files)
    changed = []
    for path, text in files.items():
        new = text
        for old, rep in NEUTRAL.items():
            new = new.replace(old, rep)
        if new != text:
            files[path] = new
            changed.append(path)
    neutral = Artifact(files)
    dom = _agentqa("hard")
    before, left = copied_strings(final.files, dom), copied_strings(neutral.files, dom)
    if left:
        raise SystemExit(f"the neutral copy still contains copied strings: {left}")
    llm = CachedLLM(ClaudeCLI("haiku", timeout_s=300), ROOT / "demo" / "runs" / ".cache_metaharness_hard")
    t0 = time.time()
    rep = rescore(dom, llm, {"final": final, "final_neutralised": neutral})
    missing = {s: {a: v["missing"] for a, v in row.items()} for s, row in rep["splits"].items()}
    out = {"replaced": NEUTRAL, "files_changed": changed, "copied_in_final": before, "copied_in_neutral": left,
           "k": RESCORE_K, "seed_offset": RESCORE_SEED_OFFSET, "started_at": t0,
           "scores": {s: {a: v["S"] for a, v in row.items()} for s, row in rep["splits"].items()},
           "families": {s: {a: v["families"] for a, v in row.items()} for s, row in rep["splits"].items()},
           "paired_vs_final": {s: row["final_neutralised"].get("vs_reference") for s, row in rep["splits"].items()},
           "missing": missing, "clean": not any(n for row in missing.values() for n in row.values()),
           "usd": round(llm.meter.total().cost_usd, 4), "cache": {"hits": llm.hits, "misses": llm.misses}}
    (RUN / "leak_ablation.json").write_text(json.dumps(out, indent=1, default=str))
    print(json.dumps(out, indent=1, default=str))
    if not out["clean"]:
        print("INFRA_ERRORS: trials missing", missing)


if __name__ == "__main__":
    main()
