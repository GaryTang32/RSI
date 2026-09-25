"""USD accounting of one run directory across processes (resume-safe budgets).

A run killed mid-way and resumed in a new process starts with fresh LLM meters, so a
budget checked against ``meter.total()`` alone forgets everything the killed process
paid for (the recorded live validation run spent $0.88 before its kill that its USD
budget never saw). :class:`SpendLedger` keeps ``<out_dir>/spend.json``::

    {"segments": [{"segment": 0, "usd": ..., "live_usd": ..., "replayed_usd": ...}, ...]}

one segment per process that worked on the directory. A process's charge is

* ``live_usd``: what its LLM backends actually paid (``meter.total()``), plus
* ``replayed_usd``: the original price of the calls a :class:`rsi.core.CachedLLM`
  replayed from its cache (``CachedLLM.saved``). On a resume these are exactly the
  killed process's calls that were not yet persisted as run state (drafts, evaluations,
  analyses are reused from disk and never replayed), so they carry the pre-kill spend
  that the last checkpoint had not recorded; on a $0 replay of a recorded run they make
  the budget bind where it bound originally.

The loop checkpoints the current segment after every persisted step (baseline and
calibration evaluations, the analysis report, each drafted candidate, each evaluation,
each settled round), so a kill loses no recorded spend, and a call replayed on resume was
never recorded before (no double counting). The budget of the resumed process is checked
against ``prior segments + this process``. The write-only shadow monitor has its own
meter and is not part of any segment.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Callable

from ..core.llm import LLM, UsageMeter

SPEND_FILE = "spend.json"


class SpendLedger:
    def __init__(self, out_dir: str | Path, llms: Callable[[], list[LLM]]) -> None:
        self.path = Path(out_dir) / SPEND_FILE
        self._llms = llms
        self._lock = threading.Lock()
        try:
            prev = json.loads(self.path.read_text()) if self.path.exists() else {}
        except (OSError, ValueError):
            prev = {}
        self.segments: list[dict] = [s for s in (prev.get("segments") or []) if isinstance(s, dict)]
        self.prior_usd = float(sum(float(s.get("usd", 0.0) or 0.0) for s in self.segments))
        self.index = len(self.segments)

    def current(self) -> dict:
        """This process's charge: live spend + original price of cache-replayed calls."""
        live = replayed = 0.0
        for l in self._llms():
            live += l.meter.total().cost_usd
            saved = getattr(l, "saved", None)
            if isinstance(saved, UsageMeter):
                replayed += saved.total().cost_usd
        return {"segment": self.index, "usd": round(live + replayed, 6), "live_usd": round(live, 6),
                "replayed_usd": round(replayed, 6)}

    def total(self) -> float:
        """USD charged to this run directory so far (earlier processes + this one)."""
        return self.prior_usd + self.current()["usd"]

    def summary(self) -> dict:
        cur = self.current()
        return {"prior_segments_usd": round(self.prior_usd, 6), "this_process": cur,
                "run_total_usd": round(self.prior_usd + cur["usd"], 6), "n_segments": self.index + 1}

    def checkpoint(self) -> None:
        """Record this process's segment (atomic write)."""
        cur = self.current()
        with self._lock:
            data = {"segments": self.segments[: self.index] + [cur]}
            tmp = self.path.with_name(self.path.name + ".tmp")
            try:
                tmp.write_text(json.dumps(data, indent=1))
                tmp.replace(self.path)
            except OSError:
                pass
