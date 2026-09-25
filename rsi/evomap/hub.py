"""Hubs: the shared-library side (spec §3.9, §4.16-4.18, §9.1).

One :class:`Hub` interface, two implementations:

* :class:`NaiveEvoMapHub` (this module) - a faithful replica of the public
  network's incentives for the Behind-EvoMap experiments: asset_id + structure +
  substance checks, validator nodes that run the *proposer's own* commands in an
  **empty** directory, promotion on the **self-reported** ValidationReport,
  +100 credits per promotion and +5 per fetch, GDI ranking
  ``0.35*Intrinsic + 0.30*Usage + 0.20*Social + 0.15*Freshness`` computed from
  claimed metadata;
* :class:`~rsi.evomap.safehub.SafeHub` - verifiable execution, adoption-based
  ranking, adoption-only credits (§9.2).

Also here: :class:`Bundle` (Gene + Capsule + EvolutionEvent + proof material),
:class:`AssetRecord`, :class:`CreditLedger` (Gini, top-10% share) and the
client-side reuse score (§4.16).
"""
from __future__ import annotations

import copy
import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional, Protocol, Sequence

from rsi.core import Ledger, Node
from rsi.core.stats import gini

from .assets import Gene
from .hashing import verify_asset_id
from .signals import cosine, is_problem, pattern_hits, tokens
from .validation import CommandPolicy, InProcessExecutor, ValidationRunner

PROMOTION_CREDIT = 100.0
FETCH_CREDIT = 5.0
VALIDATOR_CREDIT = 20.0
STARTER_CREDITS = 500.0


@dataclass
class Bundle:
    """What a publisher sends. ``pre_state`` / ``post_state`` are optional proof
    material (the workspace before/after the capsule's diff) - not GEP fields;
    only a hub that verifies execution uses them."""

    gene: dict
    capsule: Optional[dict] = None
    event: Optional[dict] = None
    report: Optional[dict] = None                  # self-reported ValidationReport
    pre_state: Optional[dict] = None
    post_state: Optional[dict] = None
    meta: dict = field(default_factory=dict)       # simulator annotations (never read by hubs)

    @property
    def gene_obj(self) -> Gene:
        return Gene.from_dict(self.gene)


@dataclass
class Decision:
    accepted: bool
    status: str
    asset_id: Optional[str] = None
    reasons: list[str] = field(default_factory=list)
    report: Optional[dict] = None


@dataclass
class AssetView:
    asset_id: str
    gene: dict
    capsule: Optional[dict]
    score: float                       # hub ranking score
    status: str
    author: str
    similarity: float = 0.0
    exploration: bool = False


@dataclass
class AssetRecord:
    asset_id: str
    bundle: Bundle
    author: str
    status: str
    epoch: int
    fetches: list = field(default_factory=list)          # (consumer, epoch)
    reviews: list = field(default_factory=list)          # (consumer, rating, epoch)
    adoptions: list = field(default_factory=list)        # dicts: consumer, outcome, epoch, counted, reason, uplift
    validator_reports: list = field(default_factory=list)
    hub_report: Optional[dict] = None
    reasons: list = field(default_factory=list)
    lineage: Optional[str] = None
    version: int = 1
    promoted_epoch: Optional[int] = None

    @property
    def gene(self) -> Gene:
        return self.bundle.gene_obj

    def claimed(self) -> dict:
        c = self.bundle.capsule or {}
        return {"confidence": float(c.get("confidence", 0.0) or 0.0),
                "success_streak": int(c.get("success_streak", 0) or 0),
                "score": float((c.get("outcome") or {}).get("score", 0.0) or 0.0),
                "files": int((c.get("blast_radius") or {}).get("files", 0) or 0),
                "lines": int((c.get("blast_radius") or {}).get("lines", 0) or 0),
                "report_ok": bool((self.bundle.report or {}).get("overall_ok", False))}

    def adopters(self, counted_only: bool = True) -> set:
        return {a["consumer"] for a in self.adoptions if a["consumer"] != self.author and
                (a.get("counted") or not counted_only)}


class Hub(Protocol):
    epoch: int

    def publish(self, bundle: Bundle, author: str) -> Decision: ...
    def search(self, signals: Sequence[str], k: int = 5, consumer: Optional[str] = None) -> list[AssetView]: ...
    def fetch(self, asset_id: str, consumer: str) -> Optional[Bundle]: ...
    def report_outcome(self, asset_id: str, consumer: str, outcome: int, proof: Optional[dict] = None) -> dict: ...
    def rank(self, signal_class: Optional[str] = None) -> list[tuple[str, float]]: ...
    def metrics(self) -> dict: ...
    def advance_epoch(self) -> None: ...


class CreditLedger:
    """Credit balances + history. ``earned`` excludes starter credits."""

    def __init__(self, starter: float = STARTER_CREDITS) -> None:
        self.starter = starter
        self.balance: dict[str, float] = {}
        self.history: list[tuple] = []           # (epoch, agent, amount, reason)

    def register(self, agent: str) -> None:
        self.balance.setdefault(agent, self.starter)

    def credit(self, agent: str, amount: float, reason: str, epoch: int = 0) -> None:
        self.register(agent)
        self.balance[agent] += amount
        self.history.append((epoch, agent, float(amount), reason))

    def earned(self, agent: str) -> float:
        return sum(a for _, ag, a, _ in self.history if ag == agent)

    def earned_all(self, agents: Optional[Sequence[str]] = None) -> dict[str, float]:
        agents = list(agents) if agents is not None else sorted(self.balance)
        out = {a: 0.0 for a in agents}
        for _, ag, amt, _ in self.history:
            if ag in out:
                out[ag] += amt
        return out

    def gini(self, agents: Optional[Sequence[str]] = None) -> float:
        vals = [max(0.0, v) for v in self.earned_all(agents).values()]
        return gini(vals) if vals else 0.0

    def top_share(self, q: float = 0.10, agents: Optional[Sequence[str]] = None) -> float:
        vals = sorted((max(0.0, v) for v in self.earned_all(agents).values()), reverse=True)
        tot = sum(vals)
        if not vals or tot <= 0:
            return 0.0
        k = max(1, int(math.ceil(q * len(vals))))
        return sum(vals[:k]) / tot

    def by_reason(self) -> dict[str, float]:
        out: dict[str, float] = defaultdict(float)
        for _, _, a, r in self.history:
            out[r] += a
        return dict(out)


def client_reuse_score(view: AssetView, reputation: float = 50.0) -> float:
    """§4.16 r(a) = confidence * min(max(streak,1),5) * reputation/100 + 0.3 * similarity."""
    c = view.capsule or {}
    conf = float(c.get("confidence", 0.0) or 0.0)
    streak = int(c.get("success_streak", 1) or 1)
    return conf * min(max(streak, 1), 5) * (reputation / 100.0) + 0.3 * view.similarity


def reuse_threshold(signals: Sequence[str], default: float = 0.72, problem: float = 0.55) -> float:
    return problem if is_problem(signals) else default


def similarity(gene: dict, signals: Sequence[str]) -> float:
    return cosine(tokens(" ".join(signals)),
                  tokens(" ".join([*gene.get("signals_match", []), gene.get("summary", ""), gene.get("id", "")])))


# ----------------------------------------------------------------------------- GDI
@dataclass
class GDIRanker:
    """GDI = 100 * (0.35 I + 0.30 U + 0.20 S + 0.15 F). I = mean of six normalized
    metadata metrics (5 self-reported: blast radius, confidence, streak, outcome
    score, validation-report ok; 1 hub-computed: substance)."""

    w: tuple = (0.35, 0.30, 0.20, 0.15)
    fresh_half_life: float = 10.0          # epochs
    blast_files_opt: int = 2
    blast_lines_opt: int = 50

    def blast_score(self, files: int, lines: int) -> float:
        if files <= 0 or lines <= 0:
            return 0.0
        return min(1.0, self.blast_files_opt / files) * min(1.0, self.blast_lines_opt / lines)

    def intrinsic(self, rec: AssetRecord) -> tuple[float, dict]:
        cl = rec.claimed()
        g = rec.bundle.gene
        cap = rec.bundle.capsule or {}
        text = " ".join(g.get("strategy", [])) + str(cap.get("content", ""))
        m = {"blast": self.blast_score(cl["files"], cl["lines"]),
             "confidence": min(1.0, max(0.0, cl["confidence"])),
             "streak": min(max(cl["success_streak"], 0), 5) / 5.0,
             "score": min(1.0, max(0.0, cl["score"])),
             "report_ok": 1.0 if cl["report_ok"] else 0.0,
             "substance": min(1.0, len(text) / 400.0)}
        return sum(m.values()) / len(m), m

    def usage(self, rec: AssetRecord) -> float:
        n = len({c for c, _ in rec.fetches if c != rec.author})
        good = sum(1 for _, r, _ in rec.reviews if r >= 4)
        return 0.5 * min(1.0, math.log1p(n) / math.log1p(50)) + 0.5 * (good + 1) / (len(rec.reviews) + 2)

    def social(self, rec: AssetRecord) -> float:
        n = len(rec.reviews)
        rating = sum(r for _, r, _ in rec.reviews) / n / 5.0 if n else 0.0
        return 0.933 * min(1.0, n / 10.0) * rating + 0.067 * (1.0 if rec.bundle.event else 0.0)

    def freshness(self, rec: AssetRecord, epoch: int) -> float:
        return 0.5 ** (max(0, epoch - rec.epoch) / self.fresh_half_life)

    def score(self, rec: AssetRecord, epoch: int) -> float:
        I, _ = self.intrinsic(rec)
        return 100.0 * (self.w[0] * I + self.w[1] * self.usage(rec) + self.w[2] * self.social(rec) +
                        self.w[3] * self.freshness(rec, epoch))

    def components(self, rec: AssetRecord, epoch: int) -> dict:
        I, m = self.intrinsic(rec)
        return {"I": I, "U": self.usage(rec), "S": self.social(rec), "F": self.freshness(rec, epoch), **m}


# ----------------------------------------------------------------------------- base hub
class _HubBase:
    name = "hub"

    def __init__(self, *, ledger: Optional[Ledger] = None) -> None:
        self.records: dict[str, AssetRecord] = {}
        self.order: list[str] = []
        self.credits = CreditLedger()
        self.epoch = 0
        self.ledger = ledger if ledger is not None else Ledger()
        self.log: list[dict] = []
        self._pub_node: dict[str, str] = {}

    def advance_epoch(self) -> None:
        self.epoch += 1

    def _node(self, kind: str, status: str, asset_id: str, author: str, **meta) -> None:
        """Hub ledger tree: publish nodes are roots; verify/adopt/revoke nodes hang under them."""
        nid = f"{kind}_{len(self.ledger):07d}"
        parent = None if kind == "publish" else self._pub_node.get(asset_id)
        self.ledger.add(Node(id=nid, parent=parent, round=self.epoch, kind=kind, status=status,
                             change=f"{kind} {asset_id[:18]} by {author}", artifact_id=asset_id,
                             meta={"author": author, **meta}))
        if kind == "publish" and asset_id not in self._pub_node:
            self._pub_node[asset_id] = nid

    def _integrity(self, bundle: Bundle) -> list[str]:
        errs = []
        for name, a in (("gene", bundle.gene), ("capsule", bundle.capsule), ("event", bundle.event)):
            if a is None:
                continue
            if not verify_asset_id(a):
                errs.append(f"{name}: asset_id verification failed")
        return errs

    def fetch(self, asset_id: str, consumer: str) -> Optional[Bundle]:
        rec = self.records.get(asset_id)
        if rec is None or rec.status in ("rejected", "revoked"):
            return None
        rec.fetches.append((consumer, self.epoch))
        self._on_fetch(rec, consumer)
        return copy.deepcopy(rec.bundle)

    def _on_fetch(self, rec: AssetRecord, consumer: str) -> None:
        pass

    def _matching(self, signals: Sequence[str], statuses: Sequence[str]) -> list[tuple[AssetRecord, float]]:
        out = []
        for aid in self.order:
            rec = self.records[aid]
            if rec.status not in statuses:
                continue
            g = rec.bundle.gene
            hits = pattern_hits(g.get("signals_match", []), signals)
            if hits <= 0:
                continue
            out.append((rec, similarity(g, signals)))
        return out

    def published(self) -> list[AssetRecord]:
        return [self.records[a] for a in self.order]


class NaiveEvoMapHub(_HubBase):
    """Faithful replica of the public hub's self-reported regime (see module doc).

    ``promote_rule``: ``"self_report"`` (default: the bundle's own
    ValidationReport says ``overall_ok`` and claimed ``outcome.score >= 0.7``) or
    ``"validator"`` (the empty-directory validator run must pass - where only
    info-only commands like ``python --version`` can succeed)."""

    name = "naive"

    def __init__(self, *, promote_rule: str = "self_report", ranker: Optional[GDIRanker] = None,
                 validator_executor=None, ledger: Optional[Ledger] = None) -> None:
        super().__init__(ledger=ledger)
        self.promote_rule = promote_rule
        self.ranker = ranker or GDIRanker()
        self.validator = ValidationRunner(CommandPolicy.faithful(), validator_executor or InProcessExecutor(),
                                          mode="safe", retries=0)

    def publish(self, bundle: Bundle, author: str) -> Decision:
        self.credits.register(author)
        reasons = self._integrity(bundle)
        g, c = bundle.gene, bundle.capsule
        if c is None:
            reasons.append("bundle_required")
        else:
            body = max((len(str(c.get(k) or "")) for k in ("content", "diff", "strategy")), default=0)
            if body < 50:
                reasons.append("capsule substance < 50 chars")
            if len(str(c.get("summary", ""))) < 20:
                reasons.append("capsule summary < 20 chars")
        if len(str(g.get("summary", ""))) < 10:
            reasons.append("gene summary < 10 chars")
        if any(len(str(s)) < 3 for s in g.get("signals_match", [])):
            reasons.append("signal shorter than 3 chars")
        aid = g.get("asset_id") or ""
        if aid in self.records:
            reasons.append("duplicate asset_id")
        if reasons:
            self._node("publish", "rejected", aid or "none", author, reasons=reasons)
            return Decision(False, "rejected", aid, reasons)
        rec = AssetRecord(aid, bundle, author, "candidate", self.epoch)
        self.records[aid] = rec
        self.order.append(aid)
        # validator node: the proposer's own commands in a FRESH EMPTY directory
        vres = self.validator.run(list(g.get("validation", [])), {}, gene_id=g.get("id", ""), signer="validator")
        rec.validator_reports.append(vres.report.to_dict())
        self.credits.credit("validator_pool", VALIDATOR_CREDIT, "validation", self.epoch)
        cl = rec.claimed()
        eligible = cl["score"] >= 0.7 and cl["files"] > 0 and cl["lines"] > 0
        if self.promote_rule == "validator":
            ok = vres.ok and eligible
        else:
            ok = cl["report_ok"] and eligible
        rec.status = "promoted" if ok else "quarantined"
        if ok:
            rec.promoted_epoch = self.epoch
            self.credits.credit(author, PROMOTION_CREDIT, "promotion", self.epoch)
        self._node("publish", rec.status, aid, author, validator_ok=vres.ok)
        return Decision(True, rec.status, aid, [] if ok else ["not promoted (self-report / eligibility)"],
                        vres.report.to_dict())

    def _on_fetch(self, rec: AssetRecord, consumer: str) -> None:
        if consumer != rec.author:
            self.credits.credit(rec.author, FETCH_CREDIT, "fetch", self.epoch)

    def search(self, signals: Sequence[str], k: int = 5, consumer: Optional[str] = None) -> list[AssetView]:
        cands = self._matching(signals, ("promoted",))
        scored = sorted(((self.ranker.score(r, self.epoch), r, sim) for r, sim in cands),
                        key=lambda x: (-x[0], x[1].asset_id))[:k]
        return [AssetView(r.asset_id, copy.deepcopy(r.bundle.gene), copy.deepcopy(r.bundle.capsule), sc, r.status,
                          r.author, sim) for sc, r, sim in scored]

    def report_outcome(self, asset_id: str, consumer: str, outcome: int, proof: Optional[dict] = None) -> dict:
        rec = self.records.get(asset_id)
        if rec is None:
            return {"counted": False, "reason": "unknown asset"}
        score = float((proof or {}).get("score", 0.9 if outcome else 0.3))
        rating = (5 if score >= 0.85 else 4) if outcome else (1 if (proof or {}).get("violations") else 2)
        rec.reviews.append((consumer, rating, self.epoch))
        rec.adoptions.append({"consumer": consumer, "outcome": int(outcome), "epoch": self.epoch, "counted": True,
                              "reason": "self-reported review", "uplift": (proof or {}).get("uplift")})
        self._node("adopt", "reported", asset_id, consumer, outcome=int(outcome))
        return {"counted": True, "rating": rating}

    def rank(self, signal_class: Optional[str] = None) -> list[tuple[str, float]]:
        recs = [self.records[a] for a in self.order if self.records[a].status == "promoted"]
        if signal_class:
            recs = [r for r in recs if pattern_hits(r.bundle.gene.get("signals_match", []), [signal_class]) > 0]
        return sorted(((r.asset_id, self.ranker.score(r, self.epoch)) for r in recs), key=lambda x: -x[1])

    def metrics(self) -> dict:
        from .metrics import ReuseMetrics
        return ReuseMetrics().compute(self)
