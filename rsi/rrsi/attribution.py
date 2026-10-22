"""Attribution scoreboard: each declared edit's predicted tasks checked against the evaluation."""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Optional

from .evaluate import Measurement
from .history import append_lines, read_jsonl


class Scoreboard:
    def __init__(self, path: str | Path, threshold: float = 0.5) -> None:
        self.path = Path(path)
        self.threshold = threshold
        self._lock = threading.Lock()

    def rows(self) -> list[dict]:
        return read_jsonl(self.path) if self.path.exists() else []

    def recent(self, n: int = 20) -> list[dict]:
        return self.rows()[-n:]

    def has(self, t: int, variant: str) -> bool:
        return any(r.get("t") == t and r.get("variant") == variant for r in self.rows())

    def attribute(self, t: int, variant: str, edits: list, inc: Measurement, cand: Measurement,
                  threshold: Optional[float] = None) -> list[dict]:
        """Hits = predicted tasks that improved; unpredicted regressions = drops of at least ``threshold``."""
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
            append_lines(self.path, [json.dumps(r, ensure_ascii=False) + "\n" for r in rows])
        return rows

    def truncate_after(self, t: int) -> None:
        rows = [r for r in self.rows() if int(r.get("t", -1)) <= t]
        with self._lock:
            self.path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
