"""Does leaked text in a kept harness drive its sealed scores?

The hard Meta-Harness run's final prompts use strings copied from practice traces as format examples: the exact
answer of one practice task (``2379.20``) and three answer names (``Harper``, ``Delta``, ``Cobalt``). One of them,
``Delta``, also happens to be the answer to a sealed held-out task (the customer pool is small). This re-scores the
final harness and a copy with those strings replaced by neutral ones that are not in any task's answer pool, on the
sealed splits, with the same model and the same trial seeds::

    python experiments/demo/leak_ablation.py      # writes demo/runs/metaharness_hard/leak_ablation.json

If the scores hold, the leaked strings did not drive the result; if they drop, they did.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rsi.core import Artifact, CachedLLM, ClaudeCLI, transfer_report  # noqa: E402
from rsi.domains.agentqa import AgentQADomain  # noqa: E402
from rsi.domains.agentqa.hard import OOD_HINT, make_hard_suite  # noqa: E402

RUN = ROOT / "demo" / "runs" / "metaharness_hard"
NEUTRAL = {"2379.20": "1234.50", "`Harper`, `Delta`, `Cobalt`": "`Ingrid`, `Orion`, `Vesper`"}


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
    dom = AgentQADomain(make_hard_suite(n_evolve=8, n_holdout=6, n_ood_per_family=3, seed=0), ood_hint=OOD_HINT)
    llm = CachedLLM(ClaudeCLI("haiku", timeout_s=300), ROOT / "demo" / "runs" / ".cache_metaharness_hard")
    rep = transfer_report(dom, llm, {"final": final, "final_neutralised": neutral}, splits=("holdout", "ood"),
                          k=2, workers=4, reference="final")
    out = {"replaced": NEUTRAL, "files_changed": changed,
           "scores": {s: {a: v["S"] for a, v in row.items()} for s, row in rep["splits"].items()},
           "paired_vs_final": {s: row["final_neutralised"].get("vs_reference") for s, row in rep["splits"].items()},
           "usd": round(llm.meter.total().cost_usd, 4), "cache": {"hits": llm.hits, "misses": llm.misses}}
    (RUN / "leak_ablation.json").write_text(json.dumps(out, indent=1, default=str))
    print(json.dumps(out, indent=1, default=str))


if __name__ == "__main__":
    main()
