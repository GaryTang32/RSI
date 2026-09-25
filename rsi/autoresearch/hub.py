"""Collaborative autoresearch ("autoresearch at home", SETI@home style).

:class:`SharedHub` replaces the Ensue key-value service of
``mutable-state-inc/autoresearch-at-home`` with a directory of JSON files guarded
by ``flock`` (safe across threads and processes). It reproduces
``coordinator.py``:

* **claim before run** - key ``<agent>--<slug(desc)>--<sha256(desc)[:6]>``; skip if a
  result exists for that key or a claim on it is younger than ``CLAIM_TTL`` (900 s);
  skip if a *semantic* search over fresh claims finds one with similarity >=
  ``SEMANTIC_THRESHOLD`` (0.92, top-5); otherwise write the claim, wait
  ``VERIFY_DELAY`` (2 s), re-read, keep it only if it is still ours (last writer
  wins). The exact key embeds the agent name, so cross-agent dedup rests on the
  semantic check (faithful); ``dedup_on_code=True`` adds a code-hash check.
* **publish results with full source** (``results/<key>``), agent best (no sanity
  checks), global best (reject v <= 0, v < 0.5, one-step jump > 0.1; read-compare-
  write with a second read) and tier best (weaker: no jump check, no second read).
* **insights and hypotheses** namespaces, ``unclaimed_hypotheses`` (a semantic
  search that never filters tested ones), ``analyze`` and the leaderboard.

Results are self-reported. ``verifier`` (hardened) re-runs a claimed best
independently before it may become the global best - the fix for the at-home
weakness that any participant can write the best.

:class:`CollaborativeLoop` is one participant: THINK (sync every 5 runs), CLAIM
(<= 5 alternatives, then "just run something"), run, PUBLISH (result + insight +
hypothesis), keep vs its own branch tip, and adopt the swarm's best when it is
better. ``report`` lets a test agent lie about its numbers.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import re
import threading
import time
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from ..core.artifact import Artifact
from ..core.ledger import Node, new_id
from .loop import AutoresearchLoop

CLAIM_TTL = 900.0
VERIFY_DELAY = 2.0
SEMANTIC_THRESHOLD = 0.92
MAX_CLAIM_ATTEMPTS = 5
SYNC_EVERY_N = 5


def slugify(text: str, max_len: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(text).lower().strip()).strip("-")
    return slug[:max_len].rstrip("-")


def experiment_key(agent_id: str, description: str) -> str:
    h = hashlib.sha256(description.lower().strip().encode()).hexdigest()[:6]
    return f"{slugify(agent_id, 20) or 'unknown'}--{slugify(description)}--{h}"


class NgramCosine:
    """Character 3-gram TF cosine: an offline stand-in for embedding similarity."""

    def __init__(self, n: int = 3) -> None:
        self.n = n

    def _vec(self, s: str) -> Counter:
        s = f"  {' '.join(str(s).lower().split())}  "
        return Counter(s[i:i + self.n] for i in range(len(s) - self.n + 1))

    def __call__(self, a: str, b: str) -> float:
        va, vb = self._vec(a), self._vec(b)
        dot = sum(v * vb.get(k, 0) for k, v in va.items())
        na = math.sqrt(sum(v * v for v in va.values()))
        nb = math.sqrt(sum(v * v for v in vb.values()))
        return dot / (na * nb) if na and nb else 0.0


class TokenJaccard:
    def __call__(self, a: str, b: str) -> float:
        ta, tb = set(str(a).lower().split()), set(str(b).lower().split())
        return len(ta & tb) / len(ta | tb) if ta | tb else 1.0


@dataclass
class SanityRule:
    """Heuristic plausibility filters (at-home: reject v <= 0, v < 0.5, jump > 0.1)."""

    direction: str = "min"
    floor: Optional[float] = 0.5        # min-direction: reject values below; max-direction: reject above
    max_jump: Optional[float] = 0.1
    reject_nonpositive: bool = True

    def check(self, v: float, current: Optional[float], jump: bool = True) -> Optional[str]:
        if self.reject_nonpositive and v <= 0:
            return f"value {v} <= 0 (likely a crash/bug)"
        if self.floor is not None:
            if self.direction == "min" and v < self.floor:
                return f"value {v} < {self.floor} (suspiciously good)"
            if self.direction == "max" and v > self.floor:
                return f"value {v} > {self.floor} (suspiciously good)"
        if jump and self.max_jump is not None and current is not None:
            imp = (current - v) if self.direction == "min" else (v - current)
            if imp > self.max_jump:
                return f"improvement {imp:.4f} > {self.max_jump} in one step (suspicious)"
        return None


class SharedHub:
    """File-backed collaboration hub (see module docstring)."""

    NAMESPACES = ("claims", "results", "best", "hypotheses", "insights")

    def __init__(self, root: str | Path, *, direction: str = "min", metric: str = "val_bpb",
                 claim_ttl: float = CLAIM_TTL, verify_delay: float = VERIFY_DELAY,
                 semantic_threshold: float = SEMANTIC_THRESHOLD, similarity: Optional[Callable] = None,
                 sanity: object = "at-home", verifier: Optional[Callable[[dict], Optional[float]]] = None,
                 verify_tol: float = 0.0, dedup_on_code: bool = False, now: Callable[[], float] = time.time,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.root = Path(root)
        for ns in self.NAMESPACES:
            (self.root / ns).mkdir(parents=True, exist_ok=True)
        self.direction, self.metric = direction, metric
        self.claim_ttl, self.verify_delay = claim_ttl, verify_delay
        self.threshold = semantic_threshold
        self.sim = similarity or NgramCosine()
        if isinstance(sanity, str):
            if sanity != "at-home":
                raise ValueError("sanity must be 'at-home', a SanityRule or None")
            # at-home's thresholds (v >= 0.5, jump <= 0.1) are val_bpb-scale; for a higher-is-better metric
            # only the "v <= 0 is a crash/bug" check carries over - pass your own SanityRule for other scales
            sanity = SanityRule() if direction == "min" else SanityRule(direction="max", floor=None, max_jump=None)
        if sanity is not None and sanity.direction != direction:
            raise ValueError(f"SanityRule direction {sanity.direction!r} != hub direction {direction!r}")
        self.sanity = sanity
        self.verifier = verifier
        self.verify_tol = verify_tol
        self.dedup_on_code = dedup_on_code
        self.now, self.sleep = now, sleep
        self._tlock = threading.RLock()
        self.log: list[str] = []
        lb = self.root / "leaderboard.json"
        if not lb.exists():
            self._write(lb, {"entries": [], "updated_at": None})

    # ---- storage
    @contextmanager
    def _locked(self):
        with self._tlock, open(self.root / ".lock", "w") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

    def _path(self, ns: str, key: str) -> Path:
        return self.root / ns / f"{key.replace('/', '__')}.json"

    @staticmethod
    def _write(p: Path, value: dict) -> None:
        tmp = p.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(value, default=str))
        os.replace(tmp, p)

    def get(self, ns: str, key: str) -> Optional[dict]:
        p = self._path(ns, key)
        try:
            return json.loads(p.read_text())
        except (OSError, json.JSONDecodeError):
            return None

    def put(self, ns: str, key: str, value: dict) -> None:
        self._write(self._path(ns, key), value)

    def items(self, ns: str) -> list[tuple[str, dict]]:
        out = []
        for p in sorted((self.root / ns).glob("*.json")):
            try:
                out.append((p.stem, json.loads(p.read_text())))
            except (OSError, json.JSONDecodeError):
                continue
        return out

    def search(self, ns: str, query: str, limit: int = 5, field: str = "description") -> list[tuple[float, str, dict]]:
        scored = [(self.sim(query, str(v.get(field, ""))), k, v) for k, v in self.items(ns)]
        return sorted(scored, key=lambda t: -t[0])[:limit]

    def _say(self, msg: str) -> None:
        self.log.append(msg)

    # ---- setup
    def seed(self, source: dict[str, str]) -> None:
        """setup_hub.py: best = the baseline source with no score yet."""
        with self._locked():
            if self.get("best", "metadata") is None:
                self.put("best", "train_py", {"source": source})
                self.put("best", "metadata", {self.metric: None, "status": "baseline", "agent_id": "hub-setup"})

    # ---- claims
    def check_claimed(self, key: str, code_hash: Optional[str] = None) -> bool:
        if self.get("results", key) is not None:
            return True
        c = self.get("claims", key)
        if c and self.now() - float(c.get("claimed_at", 0)) < self.claim_ttl:
            return True
        if self.dedup_on_code and code_hash:
            for _, v in self.items("claims"):
                if v.get("code_hash") == code_hash and self.now() - float(v.get("claimed_at", 0)) < self.claim_ttl:
                    return True
            for _, v in self.items("results"):
                if v.get("code_hash") == code_hash:
                    return True
        return False

    def check_similar_claimed(self, description: str) -> list[dict]:
        out = []
        for score, _, v in self.search("claims", description, limit=5):
            if score >= self.threshold and self.now() - float(v.get("claimed_at", 0)) < self.claim_ttl:
                out.append({"description": v.get("description"), "score": score, "agent": v.get("agent_id")})
        return out

    def claim(self, description: str, agent_id: str, code_hash: Optional[str] = None) -> Optional[str]:
        """Claim an experiment; the key, or None if taken / similar work in progress."""
        key = experiment_key(agent_id, description)
        try:
            if self.check_claimed(key, code_hash):
                self._say(f"{agent_id}: already claimed/completed {key}")
                return None
            sim = self.check_similar_claimed(description)
            if sim:
                self._say(f"{agent_id}: similar work in progress: {sim[0]}")
                return None
            self.put("claims", key, {"agent_id": agent_id, "description": description, "experiment_key": key,
                                     "claimed_at": self.now(), "expected_duration_seconds": 300,
                                     "status": "claimed", "code_hash": code_hash})
            if self.verify_delay:
                self.sleep(self.verify_delay)
            c = self.get("claims", key)
            if c and c.get("agent_id") == agent_id:
                return key
            self._say(f"{agent_id}: lost claim race for {key}")
            return None
        except OSError as e:  # "Network is additive, never blocking": train locally on hub errors
            self._say(f"claim error {e!r}")
            return key

    # ---- results and bests
    def _better(self, a: float, b: float) -> bool:
        return a < b if self.direction == "min" else a > b

    def publish_result(self, key: str, agent_id: str, value: float, memory_gb: float, status: str,
                       description: str, source: dict[str, str], *, tier: Optional[str] = None,
                       code_hash: Optional[str] = None, extra: Optional[dict] = None) -> dict:
        gb = self.global_best_value()
        ab = (self.get("best", f"agent__{slugify(agent_id, 20)}") or {}).get(self.metric)
        rec = {"agent_id": agent_id, self.metric: value, "memory_gb": memory_gb, "vram_tier": tier, "status": status,
               "description": description, "source": dict(source), "completed_at": self.now(),
               "delta_vs_best": (value - gb) if gb is not None else None, "global_best_at_publish": gb,
               "delta_vs_own_best": (value - ab) if ab is not None else None, "agent_best_at_publish": ab,
               "code_hash": code_hash, **(extra or {})}
        self.put("results", key, rec)
        flags = {"global": False, "tier": False, "agent": False}
        if status == "keep":
            flags["agent"] = self._update_agent_best(agent_id, value, rec)
            flags["global"] = self.maybe_update_best(value, rec, source)
            if tier:
                flags["tier"] = self._update_tier_best(tier, value, rec, source)
        return flags

    def _update_agent_best(self, agent_id: str, value: float, rec: dict) -> bool:
        k = f"agent__{slugify(agent_id, 20)}"
        with self._locked():
            cur = (self.get("best", k) or {}).get(self.metric)
            if cur is not None and not self._better(value, cur):
                return False
            self.put("best", k, {"agent_id": agent_id, self.metric: value, "description": rec["description"],
                                 "achieved_at": self.now(), "previous_best": cur})
        return True

    def global_best_value(self) -> Optional[float]:
        m = self.get("best", "metadata")
        return None if m is None else m.get(self.metric)

    def maybe_update_best(self, value: float, rec: dict, source: dict[str, str]) -> bool:
        """Global best: sanity filters, read-compare-write with a second read, optional verification."""
        with self._locked():
            cur_meta = self.get("best", "metadata") or {}
            cur = cur_meta.get(self.metric)
            if self.sanity is not None:
                why = self.sanity.check(value, cur, jump=True)
                if why:
                    self._say(f"REJECTED best update: {why}")
                    return False
            if cur is not None and not self._better(value, cur):
                return False
        if self.verifier is not None:
            verified = self.verifier(source)
            if verified is None or (cur is not None and not self._better(verified, cur)) or \
                    abs(verified - value) > self.verify_tol and self._better(value, verified):
                self._say(f"REJECTED best update: claimed {value:.6f}, independent re-run gave {verified}")
                return False
            value = verified if self._better(value, verified) else value
        with self._locked():
            cur2 = (self.get("best", "metadata") or {}).get(self.metric)
            if cur2 is not None and not self._better(value, cur2):
                self._say("lost best-update race")
                return False
            self.put("best", "train_py", {"source": dict(source)})
            self.put("best", "metadata", {**{k: v for k, v in rec.items() if k != "source"}, self.metric: value,
                                          "achieved_at": self.now(), "previous_best": cur2,
                                          "previous_best_by": cur_meta.get("agent_id"),
                                          "verified": self.verifier is not None})
            self._leaderboard(rec, value)
        return True

    def _update_tier_best(self, tier: str, value: float, rec: dict, source: dict[str, str]) -> bool:
        """Weaker than the global rule (faithful): no jump check and no second read."""
        if self.sanity is not None and self.sanity.check(value, None, jump=False):
            return False
        cur = (self.get("best", f"tier__{tier}__metadata") or {}).get(self.metric)
        if cur is not None and not self._better(value, cur):
            return False
        self.put("best", f"tier__{tier}__train_py", {"source": dict(source)})
        self.put("best", f"tier__{tier}__metadata", {**{k: v for k, v in rec.items() if k != "source"},
                                                     self.metric: value, "previous_best": cur})
        return True

    def _leaderboard(self, rec: dict, value: float) -> None:
        p = self.root / "leaderboard.json"
        lb = json.loads(p.read_text()) if p.exists() else {"entries": []}
        lb["entries"].append({"agent_id": rec.get("agent_id"), self.metric: value,
                              "description": rec.get("description"), "t": self.now()})
        lb["updated_at"] = self.now()
        self._write(p, lb)

    def pull_best_config(self, tier: Optional[str] = None) -> Optional[tuple[dict, dict]]:
        """(source files, metadata) of the tier best (fallback: global best)."""
        if tier:
            m, c = self.get("best", f"tier__{tier}__metadata"), self.get("best", f"tier__{tier}__train_py")
            if m and c:
                return c["source"], m
        m, c = self.get("best", "metadata"), self.get("best", "train_py")
        if m and c and m.get(self.metric) is not None:
            return c["source"], m
        return None

    # ---- knowledge
    def post_insight(self, agent_id: str, insight: str, evidence_keys=()) -> None:
        h = hashlib.sha256(insight.encode()).hexdigest()[:6]
        self.put("insights", f"{slugify(agent_id, 20)}--{slugify(insight)}--{h}",
                 {"agent_id": agent_id, "insight": insight, "description": insight,
                  "evidence_keys": list(evidence_keys), "posted_at": self.now()})

    def publish_hypothesis(self, agent_id: str, title: str, hypothesis: str, suggested_config: Optional[dict] = None,
                           evidence_keys=(), priority: int = 3) -> None:
        h = hashlib.sha256(title.encode()).hexdigest()[:6]
        self.put("hypotheses", f"{slugify(agent_id, 20)}--{slugify(title)}--{h}",
                 {"agent_id": agent_id, "title": title, "hypothesis": hypothesis, "description": title,
                  "suggested_config": suggested_config or {}, "evidence_keys": list(evidence_keys),
                  "priority": priority, "created_at": self.now()})

    def unclaimed_hypotheses(self, topic: str = "experiment", limit: int = 10) -> list[dict]:
        """Faithful: a semantic search over hypotheses, never filtered by tested/claimed."""
        return [v for _, _, v in self.search("hypotheses", topic, limit=limit)]

    def analyze(self) -> dict:
        res = [v for _, v in self.items("results")]
        keeps = [v for v in res if v.get("status") == "keep"]
        vals = sorted((v[self.metric] for v in keeps), reverse=self.direction == "max")
        # faithful quirk: compares best 5 vs next 5 keeps sorted by value (nearly always "improving")
        trend = None
        if len(vals) >= 10:
            trend = "improving" if sum(vals[:5]) != sum(vals[5:10]) else "flat"
        by_time = [v[self.metric] for v in sorted(keeps, key=lambda v: v.get("completed_at", 0))]
        return {"n_results": len(res), "n_keep": len(keeps), "global_best": self.global_best_value(),
                "agents": sorted({v["agent_id"] for v in res}), "improvement_trend_faithful": trend,
                "keeps_by_time": by_time, "n_claims": len(self.items("claims")),
                "n_insights": len(self.items("insights")), "n_hypotheses": len(self.items("hypotheses"))}


class CollaborativeLoop(AutoresearchLoop):
    """One swarm participant (see module docstring).

    ``report(value) -> value`` is what the agent publishes (identity for honest
    agents); ``claiming=False`` disables the claim protocol (ablation).
    """

    def __init__(self, *args, hub: SharedHub, agent_id: str, tier: Optional[str] = None, claiming: bool = True,
                 sync_every: int = SYNC_EVERY_N, report: Optional[Callable[[float], float]] = None,
                 adopt: bool = True, **kw) -> None:
        super().__init__(*args, **kw)
        if hub.direction != self.task.direction:
            raise ValueError(f"hub direction {hub.direction!r} != task direction {self.task.direction!r}")
        self.hub, self.agent_id, self.tier = hub, agent_id, tier
        self.claiming, self.sync_every, self.adopt = claiming, sync_every, adopt
        self.report = report or (lambda v: v)
        self.hub_stats = {"claims_ok": 0, "claims_refused": 0, "ran_unclaimed": 0, "adoptions": 0, "published": 0}
        self.started: list[dict] = []

    def _adopt_if_better(self) -> None:
        got = self.hub.pull_best_config(self.tier) if self.adopt else None
        if not got:
            return
        source, meta = got
        v = meta.get(self.hub.metric)
        mine = self.inc["samples"].mean if self.inc.get("samples") else None
        if v is None or (mine is not None and not self.task.better(v, mine)):
            return
        art = self.inc["artifact"].with_files(source)
        if art == self.inc["artifact"]:
            return
        sha = self.ws.commit(art, f"adopt global best ({self.hub.metric}={v:.6f} from {meta.get('agent_id')})")
        from .keep import Samples

        node = Node(id=new_id("exp"), parent=self.inc.get("node"), round=self.n_rounds, kind="adopt", status="keep",
                    score=v, change=f"adopt global best from {meta.get('agent_id')}", artifact_id=art.id,
                    meta={"commit": sha[:7], "adopted_value": v})
        self.store.put(art)
        self.ledger.add(node)
        self.inc = {"sha": sha, "artifact": art, "node": node.id,
                    "samples": Samples([v], memory_gb=meta.get("memory_gb", 0.0), artifact_id=art.id)}
        self.keeps.append((node.id, self.inc["samples"]))
        self.hub_stats["adoptions"] += 1

    def baseline(self):
        node = super().baseline()
        self.hub.seed(self.inc["artifact"].files)
        self._adopt_if_better()
        return node

    def _propose(self, ctx):
        if self.n_rounds > 1 and self.sync_every and (self.n_rounds - 1) % self.sync_every == 0:
            self._adopt_if_better()
            ctx.artifact = self.inc["artifact"]
        last = None
        for _ in range(MAX_CLAIM_ATTEMPTS):
            prop = super()._propose(ctx)
            if not prop.ok:
                return prop
            last = prop
            if not self.claiming:
                break
            key = self.hub.claim(prop.change or "", self.agent_id, code_hash=prop.artifact.id)
            if key:
                self.hub_stats["claims_ok"] += 1
                prop.meta["hub_key"] = key
                break
            self.hub_stats["claims_refused"] += 1
        else:
            self.hub_stats["ran_unclaimed"] += 1        # "a rare duplicate beats doing nothing"
        if last is not None:
            self.started.append({"agent": self.agent_id, "description": last.change, "code": last.artifact.id,
                                 "t": time.time()})
            last.meta.setdefault("hub_key", experiment_key(self.agent_id, last.change or ""))
        return last

    def _record(self, **kw):
        node = super()._record(**kw)
        if node.status in ("keep", "discard", "crash") and node.kind == "candidate":
            art: Artifact = kw["art"]
            samples = kw.get("samples")
            v = samples.mean if samples is not None and node.status != "crash" else 0.0
            rep = self.report(v) if node.status != "crash" else 0.0
            key = experiment_key(self.agent_id, node.change)
            self.hub.publish_result(key, self.agent_id, rep, samples.memory_gb if samples else 0.0, node.status,
                                    node.change, art.files, tier=self.tier, code_hash=art.id)
            self.hub.post_insight(self.agent_id, f"{node.change}: {node.status} ({rep:.4f})", [key])
            if node.status == "keep":
                self.hub.publish_hypothesis(self.agent_id, f"push further: {node.change}",
                                            "the last change helped; the same direction may help again", {}, [key])
            self.hub_stats["published"] += 1
        return node

    def finish(self):
        res = super().finish()
        res.meta["hub"] = dict(self.hub_stats)
        res.meta["started"] = list(self.started)
        return res
