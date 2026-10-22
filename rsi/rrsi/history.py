"""Edit history L_t (one JSONL record per edit) and its summaries T_t, g_t, U_t, sigma_t, B_t."""
from __future__ import annotations

import json
import math
import threading
import time
from pathlib import Path
from typing import Callable, Optional, Sequence

from .components import K as K_DEFAULT

RENDER_KEYS = ("t", "variant", "edit_id", "component", "hypothesis", "targets_mode", "delta_S", "delta_C",
               "accepted", "outcome", "bundle", "detail")


def _r(x: Optional[float], nd: int) -> Optional[float]:
    return None if x is None else round(float(x), nd)


class History:
    """Append-only JSONL ledger of per-edit records."""

    def __init__(self, path: str | Path, K: Sequence[str] = tuple(K_DEFAULT), timestamps: bool = True) -> None:
        self.path = Path(path)
        self.K = list(K)
        self.timestamps = timestamps
        self._lock = threading.Lock()

    def records(self) -> list[dict]:
        return read_jsonl(self.path) if self.path.exists() else []

    def append(self, rec: dict) -> None:
        self.append_many([rec])

    def append_many(self, recs: list[dict]) -> None:
        """One write, so a kill cannot leave half a candidate behind."""
        lines = []
        for rec in recs:
            rec = dict(rec)
            if self.timestamps:
                rec.setdefault("ts", time.strftime("%Y-%m-%d %H:%M:%S"))
            lines.append(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        with self._lock:
            append_lines(self.path, lines)

    def append_candidate(self, t: int, variant: str, edits: list[dict], outcome: str, delta_S: Optional[float],
                         delta_C: Optional[float], accepted: bool, S: Optional[float], C: Optional[float],
                         diff: Optional[str], detail: str = "") -> None:
        """The per-edit records of one candidate H' (one write)."""
        self.append_many([{
            "t": t, "variant": variant, "edit_id": e.get("id"), "component": e.get("component"),
            "hypothesis": e.get("hypothesis") or e.get("mechanism"), "targets_mode": e.get("targets_mode"),
            "predicted_affected": e.get("predicted_affected"), "diff": diff,
            "delta_S": _r(delta_S, 6), "delta_C": _r(delta_C, 6), "accepted": bool(accepted), "outcome": outcome,
            "S": _r(S, 6), "C": _r(C, 1), "bundle": len(edits or []), "detail": detail[:600] if detail else "",
        } for e in edits or [{"id": "C1", "component": None, "hypothesis": None}]])

    def _rewrite(self, drop: Callable[[dict], bool]) -> None:
        recs = [r for r in self.records() if not drop(r)]
        with self._lock:
            self.path.write_text("".join(json.dumps(r, ensure_ascii=False, default=str) + "\n" for r in recs))

    def replace_round(self, t: int, keep: Callable[[dict], bool] = lambda r: False) -> None:
        """Drop the per-edit records of round t (a re-adjudication rewrites them)."""
        self._rewrite(lambda r: r.get("t") == t and r.get("edit_id") and not keep(r))

    def truncate_after(self, t: int) -> None:
        """Remove every per-edit record of rounds > t."""
        self._rewrite(lambda r: r.get("edit_id") is not None and int(r.get("t", -1)) > t)

    def has(self, t: int, variant: str) -> bool:
        return any(r.get("t") == t and r.get("variant") == variant for r in self.records())

    def measured(self) -> list[dict]:
        return [r for r in self.records() if r.get("delta_S") is not None and r.get("component") in self.K]

    def tried(self) -> set[str]:
        """T_t: components with at least one measured edit."""
        return {r["component"] for r in self.measured()}

    def accepted_edits(self, before_t: Optional[int] = None) -> dict[str, list[dict]]:
        """Accepted edits per component (the machinery in the incumbent)."""
        out: dict[str, list[dict]] = {c: [] for c in self.K}
        for r in self.records():
            if r.get("accepted") and r.get("component") in out and r.get("edit_id"):
                if before_t is not None and int(r.get("t", 10 ** 9)) >= before_t:
                    continue
                out[r["component"]].append({"t": r["t"], "edit_id": r.get("edit_id"),
                                            "hypothesis": r.get("hypothesis"), "delta_S": r.get("delta_S")})
        return out

    def incumbent_component_counts(self, before_t: Optional[int] = None) -> dict[str, int]:
        return {c: len(v) for c, v in self.accepted_edits(before_t).items()}

    def yield_g(self, t: int, n_prune: int) -> dict[str, float]:
        """g_t(l) = max recent dS per tried component; -inf when nothing recent. Eq. (yield)"""
        g = {c: -math.inf for c in self.tried()}
        for r in self.measured():
            if t - int(r["t"]) <= n_prune:
                c = r["component"]
                g[c] = max(g.get(c, -math.inf), float(r["delta_S"]))
        return g

    def prune_set(self, t: int, n_prune: int) -> list[dict]:
        """B_t = {l : g_t(l) <= 0} with each component's accepted machinery. Eq. (prune)"""
        g = self.yield_g(t, n_prune)
        acc = self.accepted_edits()
        return [{"component": c, "recent_best_gain": None if g[c] == -math.inf else round(g[c], 5),
                 "accepted_edits_in_incumbent": acc.get(c, [])} for c in sorted(g) if g[c] <= 0]

    def render(self, n: int = 40, mode: str = "full") -> list[dict]:
        """The ``n`` latest records for the proposer, at most 4 unmeasured (mode: full | accepted_only | none)."""
        if mode == "none":
            return []
        recs = self.records()
        if mode == "accepted_only":
            recs = [r for r in recs if r.get("accepted")]
        kept, unmeasured = [], 0
        for r in reversed(recs):
            if r.get("delta_S") is None:
                unmeasured += 1
                if unmeasured > 4:
                    continue
            kept.append(r)
            if len(kept) >= n:
                break
        return [{k: r.get(k) for k in RENDER_KEYS if r.get(k) is not None} for r in reversed(kept)]


def stall_flag(trajectory: Sequence[float], t: int, w: int, delta: float) -> int:
    """sigma_t = 1[S_t - S_{t-w} <= delta]; 0 while fewer than w rounds exist. Eq. (explore)"""
    if t < w or t >= len(trajectory) or t - w < 0:
        return 0
    return int(trajectory[t] - trajectory[t - w] <= delta)


def exploration(t: int, stall: int, tried: set, m_draft: int, K: Sequence[str] = tuple(K_DEFAULT)) -> dict:
    """E_t = (sigma_t, U_t, m_draft) plus the text handed to the proposer."""
    untried = [c for c in K if c not in tried]
    if stall and untried:
        text = (f"STALL: the incumbent has not moved by more than the noise band over the last rounds "
                f"(sigma_t = 1). {m_draft} candidate slot(s) this round are RESERVED for exploratory edits on "
                f"components the run has never exercised: {untried}. A variant holding a reserved slot must put "
                f"at least one edit on one of those components.")
    elif untried:
        text = (f"Components not yet exercised in this run: {untried}. Not mandatory this round (sigma_t = 0), "
                f"but evidence about them is still missing.")
    else:
        text = "Every component in K has been exercised at least once."
    return {"sigma": stall, "untried": untried, "m_draft": m_draft, "text": text}


def read_jsonl(path: str | Path) -> list[dict]:
    """Records of a JSONL file, skipping a line torn by a crash mid-write."""
    out = []
    for line in Path(path).read_text().splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def append_lines(path: str | Path, lines: list[str]) -> None:
    """Append ``lines`` in one write, first terminating a torn last line."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    prefix = ""
    if p.exists() and p.stat().st_size:
        with open(p, "rb") as f:
            f.seek(-1, 2)
            if f.read(1) != b"\n":
                prefix = "\n"
    with open(p, "a") as f:
        f.write(prefix + "".join(lines))
