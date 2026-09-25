"""Memory graph advice and outcome inference (spec §4.4, §4.11, §5.7).

:class:`MemoryGraph` is an append-only list of ``MemoryGraphEvent`` dicts
(``kind`` in signal | hypothesis | attempt | outcome | external_candidate |
epoch_boundary ...). :meth:`MemoryGraph.advice` turns past outcomes into a
preferred gene and banned genes, in one of two modes:

``spec`` (GEP spec §4 / sdk 1.1.0)
    Laplace p = (s+1)/(n+2) per (signal key, gene) edge, weight w = 0.5^(age/30d),
    value = p*w; preferred = argmax value; banned iff n >= 2 and value < 0.18.
    Key matching is exact (sdk 1.1.0) or Jaccard >= 0.34 (spec text).

``current`` (Evolver v1.94.0)
    For every historical key k with Jaccard(current, k) >= 0.34: edge value
    p*w*(1.15 if any outcome carried a predictive block) * sim; best(g) = max;
    gene-level prior with half-life 45 d (inert outcomes counted as successes
    there); score = best + 0.12*prior (or 0.4*prior); inert outcomes
    (``stable_no_error``) count as neither success nor failure per key; bans:
    per-key attempts (keys with sim >= 0.8) >= 4 and best < 0.15, or < 2
    per-key attempts with >= 5 prior attempts and prior < 0.1, or >= 8
    consecutive inert with no success; epochs multiply pre-epoch evidence by 0.1.

:class:`OutcomeInferrer` reproduces how the outcome of cycle t-1 reaches the
graph: *faithful* reads the last one-line ``EvolutionEvent`` JSON in the
executor's transcript first (an LLM-printed self-assessment), else the §4.11
error-delta heuristic; *safe* uses only the solidify-measured outcome.
"""
from __future__ import annotations

import json
import re
import zlib
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional, Sequence

from .signals import jaccard, signal_key

DAY_S = 86400.0


@dataclass
class Advice:
    preferred_gene_id: Optional[str] = None
    banned_gene_ids: set = field(default_factory=set)
    total_attempts: int = 0
    scores: dict = field(default_factory=dict)
    ban_reasons: dict = field(default_factory=dict)


class MemoryGraph:
    def __init__(self, path: Optional[str | Path] = None, *, clock=None, read_limit: int = 1000) -> None:
        self.path = Path(path) if path else None
        self.clock = clock
        self.read_limit = read_limit
        self.events: list[dict] = []
        self._n = 0
        if self.path and self.path.exists():
            for line in self.path.read_text().splitlines():
                if line.strip():
                    self.events.append(json.loads(line))
            self._n = len(self.events)

    def _now(self) -> float:
        return self.clock.now() if self.clock else 0.0

    def record(self, kind: str, **fields) -> dict:
        self._n += 1
        body = json.dumps(fields, sort_keys=True, default=str)
        ev = {"type": "MemoryGraphEvent", "kind": kind,
              "id": f"mge_{self._n:06d}_{zlib.crc32(body.encode()) & 0xffffffff:08x}", "ts": self._now(), **fields}
        self.events.append(ev)
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a") as f:
                f.write(json.dumps(ev, default=str) + "\n")
        return ev

    def record_outcome(self, *, signals: Sequence[str], gene_id: Optional[str], status: str, score: float,
                       note: str = "", observed: bool = True, predictive: Optional[dict] = None) -> dict:
        return self.record("outcome", signal={"key": signal_key(signals), "signals": list(signals)},
                           gene={"id": gene_id}, outcome={"status": status, "score": float(score), "note": note,
                                                          "observed": observed, "predictive": predictive})

    def epoch_boundary(self, reason: str = "") -> dict:
        return self.record("epoch_boundary", reason=reason)

    def outcomes(self) -> list[dict]:
        return [e for e in self.events[-self.read_limit:] if e.get("kind") == "outcome"]

    # ------------------------------------------------------------------ advice
    def advice(self, signals: Sequence[str], genes: Iterable = (), *, mode: str = "current",
               key_match: str = "jaccard", half_life_days: float = 30.0, prior_half_life_days: float = 45.0,
               jaccard_min: float = 0.34, ban_value: float = 0.18, per_key_attempts: int = 4,
               ban_best: float = 0.15, inert_streak: int = 8) -> Advice:
        if mode == "spec":
            return self._advice_spec(signals, key_match, half_life_days, jaccard_min, ban_value)
        return self._advice_current(signals, half_life_days, prior_half_life_days, jaccard_min,
                                    per_key_attempts, ban_best, inert_streak)

    def _age_w(self, ts: float, hl: float) -> float:
        age_days = max(0.0, (self._now() - ts) / DAY_S)
        return 0.5 ** (age_days / hl)

    def _advice_spec(self, signals, key_match, hl, jmin, ban_value) -> Advice:
        cur = set(signals)
        key = signal_key(signals)
        edges: dict = defaultdict(lambda: [0, 0, 0.0])   # gene -> [succ, total, last_ts]
        for o in self.outcomes():
            gid = (o.get("gene") or {}).get("id")
            if not gid:
                continue
            sig = o.get("signal") or {}
            if key_match == "exact":
                if sig.get("key") != key:
                    continue
            elif jaccard(cur, sig.get("signals", [])) < jmin:
                continue
            e = edges[gid]
            e[0] += (o.get("outcome") or {}).get("status") == "success"
            e[1] += 1
            e[2] = max(e[2], float(o.get("ts", 0.0)))
        adv = Advice(total_attempts=sum(e[1] for e in edges.values()))
        best, best_v = None, -1.0
        for gid, (s, n, ts) in edges.items():
            v = (s + 1) / (n + 2) * self._age_w(ts, hl)
            adv.scores[gid] = v
            if v > best_v:
                best, best_v = gid, v
            if n >= 2 and v < ban_value:
                adv.banned_gene_ids.add(gid)
                adv.ban_reasons[gid] = f"spec: n={n} value={v:.3f} < {ban_value}"
        adv.preferred_gene_id = best if best is not None and best not in adv.banned_gene_ids else None
        return adv

    def _advice_current(self, signals, hl, prior_hl, jmin, per_key_attempts, ban_best, inert_streak) -> Advice:
        cur = set(signals)
        evs = self.events[-self.read_limit:]
        epoch_ts = max((float(e.get("ts", 0.0)) for e in evs if e.get("kind") == "epoch_boundary"), default=None)
        # per (key, gene) stats and per-gene prior
        per: dict = defaultdict(lambda: {"s": 0, "f": 0, "ts": 0.0, "pred": False, "sim": 0.0, "pre": False})
        prior: dict = defaultdict(lambda: {"s": 0, "n": 0, "ts": 0.0})
        seq: dict = defaultdict(list)       # gene -> list of 'S'/'F'/'I' in order
        post_epoch_seen: set = set()
        outs = [e for e in evs if e.get("kind") == "outcome"]
        for o in outs:
            gid = (o.get("gene") or {}).get("id")
            if not gid:
                continue
            oc = o.get("outcome") or {}
            ts = float(o.get("ts", 0.0))
            ok = oc.get("status") == "success"
            inert = ok and "stable_no_error" in str(oc.get("note", ""))
            pr = prior[gid]
            pr["n"] += 1
            pr["s"] += ok               # the gene-level prior still counts inert as success
            pr["ts"] = max(pr["ts"], ts)
            seq[gid].append("I" if inert else ("S" if ok else "F"))
            sigs = (o.get("signal") or {}).get("signals", [])
            sim = jaccard(cur, sigs)
            if sim < jmin:
                continue
            k = ((o.get("signal") or {}).get("key", ""), gid)
            st = per[k]
            pre = epoch_ts is not None and ts < epoch_ts
            if epoch_ts is not None and not pre:
                post_epoch_seen.add(k)
            st["sim"] = sim
            st["ts"] = max(st["ts"], ts)
            st["pre"] = st["pre"] or pre
            st.setdefault("post_s", 0)
            st.setdefault("post_f", 0)
            if inert:
                continue
            st["s" if ok else "f"] += 1
            if not pre:
                st["post_s" if ok else "post_f"] += 1
            if oc.get("predictive"):
                st["pred"] = True
        best: dict = defaultdict(float)
        key_attempts: dict = defaultdict(int)
        raw: dict = defaultdict(lambda: [0, 0])
        for (key, gid), st in per.items():
            s, f = st["s"], st["f"]
            factor = 1.0
            if epoch_ts is not None:
                if (key, gid) in post_epoch_seen:
                    s, f = st["post_s"], st["post_f"]
                else:
                    factor = 0.1
            n = s + f
            p = (s + 1) / (n + 2)
            v = p * self._age_w(st["ts"], hl) * (1.15 if st["pred"] else 1.0) * factor * st["sim"]
            if n > 0:
                best[gid] = max(best[gid], v)
            if st["sim"] >= 0.8:
                key_attempts[gid] += n
            raw[gid][0] += st["s"]
            raw[gid][1] += st["f"]
        adv = Advice(total_attempts=sum(p["n"] for p in prior.values()))
        top, top_v = None, 0.0
        for gid, pr in prior.items():
            pv = (pr["s"] + 1) / (pr["n"] + 2) * self._age_w(pr["ts"], prior_hl)
            b = best.get(gid, 0.0)
            score = b + 0.12 * pv if b > 0 else 0.4 * pv
            adv.scores[gid] = score
            rs, rf = raw[gid]
            if score > top_v and (rs + rf) > 0 and rs > rf and rs > 0:
                top, top_v = gid, score
            # bans
            if key_attempts[gid] >= per_key_attempts and b < ban_best:
                adv.banned_gene_ids.add(gid)
                adv.ban_reasons[gid] = f"current: per-key attempts {key_attempts[gid]} and best {b:.3f} < {ban_best}"
            elif key_attempts[gid] < 2 and pr["n"] >= 5 and pv < 0.1:
                adv.banned_gene_ids.add(gid)
                adv.ban_reasons[gid] = f"current: prior {pv:.3f} < 0.1 over {pr['n']} attempts"
            tail = 0
            for x in reversed(seq[gid]):
                if x == "I":
                    tail += 1
                else:
                    break
            if tail >= inert_streak and "S" not in seq[gid]:
                adv.banned_gene_ids.add(gid)
                adv.ban_reasons[gid] = f"current: {tail} consecutive inert outcomes"
        adv.preferred_gene_id = top if top not in adv.banned_gene_ids else None
        return adv


# ----------------------------------------------------------------------------- outcome inference
@dataclass
class Outcome:
    status: str
    score: float
    note: str
    observed: bool = False
    predictive: Optional[dict] = None


_EVENT_LINE = re.compile(r"^\s*\{.*\}\s*$")


def find_transcript_event(transcript: str, max_lines: int = 400) -> Optional[dict]:
    """The last single-line JSON object with type == EvolutionEvent and an outcome."""
    lines = (transcript or "").splitlines()[-max_lines:]
    for line in reversed(lines):
        if not _EVENT_LINE.match(line):
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(d, dict) and d.get("type") == "EvolutionEvent" and isinstance(d.get("outcome"), dict):
            return d
    return None


class OutcomeInferrer:
    """§4.11. ``mode="faithful"``: transcript EvolutionEvent first, else heuristic;
    ``mode="safe"``: the measured solidify outcome only (never an LLM-printed score)."""

    def __init__(self, mode: str = "faithful") -> None:
        self.mode = mode

    def infer(self, *, prev_error: bool, cur_error: bool, transcript: str = "", measured: Optional[Outcome] = None,
              prev_err_count: int = 0, cur_err_count: int = 0, recent_success: Sequence[bool] = ()) -> Outcome:
        if self.mode == "safe":
            if measured is None:
                raise ValueError("safe outcome inference needs the measured solidify outcome")
            return measured
        ev = find_transcript_event(transcript)
        if ev is not None:
            oc = ev["outcome"]
            score = oc.get("score")
            status = oc.get("status")
            if score is None:
                score = 0.75 if status == "success" else 0.25
            if status is None:
                status = "success" if float(score) >= 0.5 else "failed"
            return Outcome(str(status), float(score), "evolutionevent_observed", observed=True)
        if prev_error and not cur_error:
            st, base, note = "success", 0.85, "error_cleared"
        elif prev_error and cur_error:
            st, base, note = "failed", 0.20, "error_persisted"
        elif not prev_error and cur_error:
            st, base, note = "failed", 0.15, "new_error_appeared"
        else:
            st, base, note = "success", 0.60, "stable_no_error"
        delta = max(-0.12, min(0.12, (prev_err_count - cur_err_count) / 50.0))
        trend = 0.0
        if recent_success:
            rs = list(recent_success)[-5:]
            trend = 0.06 * (2 * (sum(rs) / len(rs)) - 1)
        pred = {"trend": trend}
        score = min(1.0, max(0.0, base + delta + max(-0.1, min(0.1, trend))))
        return Outcome(st, round(score, 3), note, observed=False, predictive=pred)
