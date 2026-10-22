"""Re-train autoresearch's baseline, previous keep and final ``train.py`` at seeds 10000-10002 on the hidden splits.

    python experiments/demo/autoresearch_hidden.py   # CPU only; writes demo/runs/autoresearch/hidden_reeval*.json
"""
from __future__ import annotations

import json
import time

import numpy as np
from _demo_common import RUNS, load_jsonl

from rsi.core import Artifact
from rsi.domains.tinylm import TinyLMTask

RUN = RUNS / "autoresearch"
COMMITS = {"fa8b780": "baseline", "d137e15": "previous keep (lr 0.02)", "931edb4": "final (hidden width 256)"}
SEEDS = (10000, 10001, 10002)


def main() -> None:
    ids = {(e.get("meta") or {}).get("commit"): e["artifact_id"] for e in load_jsonl(RUN / "ledger.jsonl")}
    task = TinyLMTask(budget_s=8.0, data_root=RUNS / ".cache_tinylm_data")
    task.prepare()
    out = {"seeds": list(SEEDS), "budget_s": 8.0, "started_at": time.time(), "versions": {}}
    for commit, label in COMMITS.items():
        aid = ids[commit]
        art = Artifact(json.loads((RUN / "artifacts" / aid[:2] / f"{aid}.json").read_text())["files"])
        runs = [task.audit(art, seed=s) for s in SEEDS]   # audit mode: hidden data visible to the locked evaluator only
        row = {"label": label, "runs": runs}
        for k in ("test_iid", "test_shift", "loop_metric_rerun"):
            vals = [r[k] for r in runs if isinstance(r.get(k), (int, float))]
            row[k] = {"mean": float(np.mean(vals)) if vals else None, "min": min(vals, default=None),
                      "max": max(vals, default=None), "n": len(vals)}
        out["versions"][commit] = row
        print(commit, label, {k: round(row[k]["mean"], 4) for k in ("test_iid", "test_shift", "loop_metric_rerun")
                              if row[k]["mean"] is not None}, flush=True)
    dest = RUN / "hidden_reeval.json"
    if dest.exists():   # training stops on wall-clock time, so another session trains a different amount
        dest = RUN / f"hidden_reeval_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.json"
    dest.write_text(json.dumps(out, indent=1, default=str))
    print("wrote", dest)


if __name__ == "__main__":
    main()
