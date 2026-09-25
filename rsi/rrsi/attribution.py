"""Attribution scoreboard (mirrors ``Run.attribute`` / ``Run.scoreboard``).

After a candidate is measured, every declared edit's predictions are checked against
the evaluation: ``predicted_affected`` task ids that improved are *hits*; tasks whose
mean dropped by at least the regression threshold (default 1/k) without being
predicted are *unpredicted regressions*. The last 20 rows are shown back to the
proposer ("over-claiming counts against you").
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Optional

from .evaluate import Measurement


class Scoreboard:
    def __init__(self, path: str | Path, threshold: float = 0.5) -> None:
        self.path = Path(path)
        self.threshold = threshold
        self._lock = threading.Lock()

    def rows(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(l) for l in self.path.read_text().splitlines() if l.strip()]

    def recent(self, n: int = 20) -> list[dict]:
        return self.rows()[-n:]

    def has(self, t: int, variant: str) -> bool:
        return any(r.get("t") == t and r.get("variant") == variant for r in self.rows())

    def attribute(self, t: int, variant: str, edits: list, inc: Measurement, cand: Measurement,
                  threshold: Optional[float] = None) -> list[dict]:
        thr = self.threshold if threshold is None else threshold
        im, cm = inc.task_means(), cand.task_means()
        rows = []
        for e in edits:
            pred = [str(p) for p in (e.get("predicted_affected") or []) if str(p) in im and str(p) in cm]
            hits = [p for p in pred if cm[p] > im[p]]
            drops = [x for x in im if x in cm and cm[x] - im[x] <= -thr]
            rows.append({"t": t, "variant": variant, "edit_id": e.get("id"), "component": e.get("component"),
                         "hypothesis": str(e.get("hypothesis"))[:160], "n_predicted": len(pred),
                         "predicted_hit": hits, "hit_rate": round(len(hits) / len(pred), 2) if pred else None,
                         "unpredicted_regressions": [d for d in drops if d not in pred][:12]})
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a") as f:
                for r in rows:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
        return rows

    def truncate_after(self, t: int) -> None:
        rows = [r for r in self.rows() if int(r.get("t", -1)) <= t]
        with self._lock:
            self.path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))

    def drop_round(self, t: int) -> None:
        rows = [r for r in self.rows() if int(r.get("t", -1)) != t]
        with self._lock:
            self.path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
