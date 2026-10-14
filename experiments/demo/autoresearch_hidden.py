"""Re-evaluate autoresearch's baseline, previous keep and final ``train.py`` on the hidden test splits over fresh
training seeds.

The run's shadow audit trained each kept version once, at the loop's training seed 0, so its hidden-test values are
single readings, and training stops on wall-clock time, so a single run is noisy. The run's own post-run
re-evaluation covered only validation. This trains each version again in audit mode (locked files restored, hidden
data visible to the locked evaluator only) at the same three seed numbers as that re-evaluation, 10000-10002, and
records the validation of the same models too. CPU only, no model calls::

    python experiments/demo/autoresearch_hidden.py    # writes demo/runs/autoresearch/hidden_reeval.json (once)

Training stops after 8 s of wall-clock time, so a seed fixes the data order but not the number of steps: results
depend on the machine and its load, and a later session trains a different amount than the run's own re-evaluation
did. Compare the splits within this file (``loop_metric_rerun`` is the same models' validation), not with
``summary.json`` ``meta.reeval``. The recorded file is never replaced; another session goes to a new file.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rsi.core import Artifact  # noqa: E402
from rsi.domains.tinylm import TinyLMTask  # noqa: E402

RUN = ROOT / "demo" / "runs" / "autoresearch"
COMMITS = {"fa8b780": "baseline", "d137e15": "previous keep (lr 0.02)", "931edb4": "final (hidden width 256)"}
SEEDS = (10000, 10001, 10002)


def main() -> None:
    led = [json.loads(line) for line in (RUN / "ledger.jsonl").read_text().splitlines() if line.strip()]
    ids = {(e.get("meta") or {}).get("commit"): e["artifact_id"] for e in led}
    task = TinyLMTask(budget_s=8.0, data_root=ROOT / "demo" / "runs" / ".cache_tinylm_data")
    task.prepare()
    out = {"seeds": list(SEEDS), "budget_s": 8.0, "started_at": time.time(), "versions": {}}
    for commit, label in COMMITS.items():
        aid = ids[commit]
        art = Artifact(json.loads((RUN / "artifacts" / aid[:2] / f"{aid}.json").read_text())["files"])
        runs = [task.audit(art, seed=s) for s in SEEDS]
        row = {"label": label, "runs": runs}
        for k in ("test_iid", "test_shift", "loop_metric_rerun"):
            vals = [r[k] for r in runs if isinstance(r.get(k), (int, float))]
            row[k] = {"mean": float(np.mean(vals)) if vals else None, "min": min(vals, default=None),
                      "max": max(vals, default=None), "n": len(vals)}
        out["versions"][commit] = row
        print(commit, label, {k: round(row[k]["mean"], 4) for k in ("test_iid", "test_shift", "loop_metric_rerun")
                              if row[k]["mean"] is not None}, flush=True)
    dest = RUN / "hidden_reeval.json"
    if dest.exists():   # a new session trains a different amount: keep the recorded one
        dest = RUN / f"hidden_reeval_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.json"
    dest.write_text(json.dumps(out, indent=1, default=str))
    print("wrote", dest)


if __name__ == "__main__":
    main()
