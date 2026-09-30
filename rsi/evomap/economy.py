"""Hub economy beyond promotion and fetch credits (retry round 2, I4; spec §4.18, §5.8).

Sources:

* the Hub guide (spec §4.18): +50 credits to the referrer and +100 to the referred agent; swarm bounties split
  **5 % to the proposer, 85 % to the solvers (by contribution weight), 10 % to the aggregator (reputation
  >= 60)**; a node that reaches 0 credits and stays inactive for 30 days goes dormant;
* the GEP Task object (sdk 1.14.0 ``task.schema.json``, spec §5.8): ``task_id``, ``status`` in
  open | claimed | completed | expired | cancelled, ``bounty_amount``, ``result_asset_id``;
* Evolver's solidify (spec §3.6): on a successful solidify with an active task the engine completes the task by
  submitting the capsule's asset_id - completion is SELF-REPORTED;
* the worker-side task ranking, ported line by line from Evolver v2 ``packages/evolver-cli/dist/taskReceiver.js``
  (itself "ported from evolver v1 ``src/gep/taskReceiver.js``"): :func:`score_task`, :func:`rank_tasks`,
  :func:`estimate_capability_match`, :func:`local_difficulty_estimate`, the three strategy weight sets.

Not specified by any source, so chosen here and documented: escrow (the proposer's credits are debited when the
bounty is posted), refund of an expired bounty to the proposer, and the time unit (1 epoch = 1 day).
The marketplace side (Recipe purchases: price per execution, ``max_concurrent``) is only a schema; its purchase
and payout flow is not described anywhere we can read, so it is not modelled.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

STRATEGY_WEIGHTS = {
    "greedy": {"roi": 0.1, "capability": 0.05, "completion": 0.05, "bounty": 0.8},
    "balanced": {"roi": 0.35, "capability": 0.3, "completion": 0.2, "bounty": 0.15},
    "conservative": {"roi": 0.25, "capability": 0.45, "completion": 0.25, "bounty": 0.05},
}
ROI_REFERENCE = 200.0
BOUNTY_REFERENCE = 100.0
SIGNAL_SIMILARITY_FLOOR = 0.15
HISTORY_WINDOW = 200
MIN_CAPABILITY_MATCH = 0.1

REFERRER_CREDIT = 50.0
REFERRED_CREDIT = 100.0
SWARM_SPLIT = {"proposer": 0.05, "solvers": 0.85, "aggregator": 0.10}
AGGREGATOR_MIN_REPUTATION = 60.0
DORMANT_AFTER_EPOCHS = 30


def _js_round(x: float, digits: int) -> float:
    f = 10 ** digits
    return math.floor(x * f + 0.5) / f


def parse_signals(signals) -> list[str]:
    if isinstance(signals, (list, tuple)):
        return [str(s).strip().lower() for s in signals if str(s).strip()]
    if isinstance(signals, str):
        import re
        return [s.strip().lower() for s in re.split(r"[,|]", signals) if s.strip()]
    return []


def _jaccard(a: Sequence[str], b: Sequence[str]) -> float:
    if not a or not b:
        return 0.0
    A, B = set(a), set(b)
    shared = len(A & B)
    return shared / (len(A) + len(B) - shared)


def estimate_capability_match(task_signals, memory_events: Sequence[dict]) -> float:
    """How much the node's own history looks like the task, in [0, 1] (``estimateCapabilityMatch``)."""
    ts = parse_signals(task_signals)
    if not ts or not memory_events:
        return 0.0
    seen: set = set()
    totals: dict = {}
    for ev in memory_events:
        sig = parse_signals(ev.get("signals"))
        if not sig:
            continue
        seen.update(sig)
        key = "|".join(sig)
        t = totals.setdefault(key, [0, 0])
        t[0] += 1
        if ev.get("status") == "success":
            t[1] += 1
    overlap = _jaccard(ts, sorted(seen))
    weighted = weight = 0.0
    for key, (total, succ) in totals.items():
        sim = _jaccard(ts, key.split("|"))
        if sim < SIGNAL_SIMILARITY_FLOOR:
            continue
        weighted += (succ + 1) / (total + 2) * sim            # Laplace smoothing
        weight += sim
    success_score = weighted / weight if weight > 0 else 0.5
    return min(1.0, overlap * 0.4 + success_score * 0.6)


def local_difficulty_estimate(task: "BountyTask") -> float:
    signal_factor = min(len(parse_signals(task.signals)) / 8, 1)
    words = len([w for w in str(task.title or "").split() if w])
    return min(1.0, signal_factor * 0.6 + min(words / 15, 1) * 0.4)


def score_task(task: "BountyTask", capability_match: float, strategy: str = "balanced") -> dict:
    w = STRATEGY_WEIGHTS[strategy]
    difficulty = task.complexity_score if task.complexity_score is not None else local_difficulty_estimate(task)
    bounty = task.bounty_amount or 0.0
    completion = task.historical_completion_rate if task.historical_completion_rate is not None else 0.5
    roi = min(bounty / (difficulty + 0.1) / ROI_REFERENCE, 1)
    bounty_norm = min(bounty / BOUNTY_REFERENCE, 1)
    composite = w["roi"] * roi + w["capability"] * capability_match + w["completion"] * completion + \
        w["bounty"] * bounty_norm
    return {"composite": _js_round(composite, 3),
            "factors": {"roi": _js_round(roi, 2), "capability": _js_round(capability_match, 2),
                        "completion": _js_round(completion, 2), "bounty": _js_round(bounty_norm, 2),
                        "difficulty": _js_round(difficulty, 2)}}


def rank_tasks(tasks: Iterable["BountyTask"], node_id: str, memory_events: Sequence[dict] = (),
               strategy: str = "balanced", min_capability: float = MIN_CAPABILITY_MATCH) -> list[dict]:
    """``rankTasks``: a task this node already holds comes first ("finishing a commitment beats starting a
    better one"), then open tasks by composite score, filtered by the capability floor once the node has a
    history. Greedy nodes without history sort by (has bounty id, bounty amount)."""
    tasks = list(tasks)
    mine = [{"task": t, "reason": "resume"} for t in tasks if t.status == "claimed" and t.claimed_by == node_id]
    open_ = [t for t in tasks if t.status == "open"]
    mem = list(memory_events)[-HISTORY_WINDOW:]
    if strategy == "greedy" and not mem:
        by_bounty = sorted(open_, key=lambda t: (-int(bool(t.bounty_id)), -(t.bounty_amount or 0.0)))
        return mine + [{"task": t, "reason": "scored", "score": score_task(t, 0.0, strategy)} for t in by_bounty]
    scored = [{"task": t, "reason": "scored",
               "score": score_task(t, estimate_capability_match(t.signals, mem), strategy)} for t in open_]
    scored = [e for e in scored if e["score"]["factors"]["capability"] >= min_capability or not mem]
    scored.sort(key=lambda e: -e["score"]["composite"])       # stable, as Array.prototype.sort
    return mine + scored


@dataclass
class BountyTask:
    task_id: str
    proposer: str
    bounty_amount: float
    signals: list = field(default_factory=list)
    title: str = ""
    status: str = "open"                      # open | claimed | completed | expired | cancelled
    claimed_by: Optional[str] = None
    created_epoch: int = 0
    expires_epoch: Optional[int] = None
    result_asset_id: Optional[str] = None
    complexity_score: Optional[float] = None
    historical_completion_rate: Optional[float] = None
    swarm: bool = False
    bounty_id: Optional[str] = None
    meta: dict = field(default_factory=dict)


class BountyBoard:
    """Hub-side bounty tasks on top of a hub's :class:`~rsi.evomap.hub.CreditLedger`."""

    def __init__(self, credits, *, reputation: Optional[dict] = None) -> None:
        self.credits = credits
        self.reputation = reputation if reputation is not None else {}
        self.tasks: dict[str, BountyTask] = {}
        self._n = 0

    def post(self, proposer: str, amount: float, signals: Sequence[str], *, title: str = "", epoch: int = 0,
             expires_in: Optional[int] = None, swarm: bool = False, **meta) -> BountyTask:
        self._n += 1
        t = BountyTask(f"task_{self._n:06d}", proposer, float(amount), list(signals), title, created_epoch=epoch,
                       expires_epoch=None if expires_in is None else epoch + expires_in, swarm=swarm,
                       bounty_id=f"bounty_{self._n:06d}", meta=meta)
        self.credits.credit(proposer, -float(amount), "bounty_escrow", epoch)
        self.tasks[t.task_id] = t
        return t

    def open_tasks(self) -> list[BountyTask]:
        return [t for t in self.tasks.values() if t.status == "open"]

    def claim(self, task_id: str, node: str) -> bool:
        t = self.tasks.get(task_id)
        if t is None or t.status != "open":
            return False
        t.status, t.claimed_by = "claimed", node
        return True

    def complete(self, task_id: str, node: str, asset_id: str, epoch: int = 0) -> bool:
        """Self-reported completion: the claimer submits a capsule asset_id; nothing checks it solves the task."""
        t = self.tasks.get(task_id)
        if t is None or t.status != "claimed" or t.claimed_by != node or not asset_id:
            return False
        t.status, t.result_asset_id = "completed", asset_id
        self.credits.credit(node, t.bounty_amount, "bounty", epoch)
        return True

    def complete_swarm(self, task_id: str, contributions: dict, aggregator: str, epoch: int = 0,
                       asset_id: str = "swarm") -> dict:
        """Swarm split: 5 % proposer, 85 % solvers by contribution weight, 10 % aggregator (reputation >= 60)."""
        t = self.tasks.get(task_id)
        if t is None or t.status not in ("open", "claimed"):
            raise ValueError("task not active")
        if self.reputation.get(aggregator, 50.0) < AGGREGATOR_MIN_REPUTATION:
            raise ValueError("aggregator reputation < 60")
        tot_w = float(sum(contributions.values()))
        if tot_w <= 0:
            raise ValueError("no contributions")
        pay = {t.proposer: SWARM_SPLIT["proposer"] * t.bounty_amount}
        for node, w in contributions.items():
            pay[node] = pay.get(node, 0.0) + SWARM_SPLIT["solvers"] * t.bounty_amount * w / tot_w
        pay[aggregator] = pay.get(aggregator, 0.0) + SWARM_SPLIT["aggregator"] * t.bounty_amount
        for node, amt in pay.items():
            self.credits.credit(node, amt, "swarm_bounty", epoch)
        t.status, t.result_asset_id = "completed", asset_id
        return pay

    def expire(self, epoch: int) -> list[str]:
        out = []
        for t in self.tasks.values():
            if t.status in ("open", "claimed") and t.expires_epoch is not None and epoch >= t.expires_epoch:
                t.status = "expired"
                self.credits.credit(t.proposer, t.bounty_amount, "bounty_refund", epoch)
                out.append(t.task_id)
        return out


def refer(credits, referrer: str, referred: str, epoch: int = 0) -> None:
    """Referral: +50 to the referrer, +100 to the referred agent."""
    credits.credit(referrer, REFERRER_CREDIT, "referral", epoch)
    credits.credit(referred, REFERRED_CREDIT, "referred", epoch)


def dormant_nodes(credits, last_active: dict, epoch: int, inactive_epochs: int = DORMANT_AFTER_EPOCHS) -> set:
    """Nodes at (or below) 0 credits whose last activity is >= 30 epochs (days) ago."""
    return {n for n, bal in credits.balance.items()
            if bal <= 0 and epoch - last_active.get(n, -10 ** 9) >= inactive_epochs}


class BountyDriver:
    """Population hook (X19): an external poster puts ``per_epoch`` bounties of ``amount`` credits on the board at
    the start of every epoch, each one built from a random task of the pool (its public signals and family);
    before a cycle, an agent with no active task ranks the board with :func:`rank_tasks` over its own event
    history and claims the head; its cycle then runs on a task of the bounty's family; after a SUCCESSFUL
    solidify it completes the bounty with the new capsule's asset_id (self-reported, as ``solidify.js``).
    Unfinished bounties expire after ``expires_in`` epochs and are refunded."""

    def __init__(self, board: BountyBoard, *, per_epoch: int = 4, amount: float = BOUNTY_REFERENCE,
                 poster: str = "bounty_poster", strategy: str = "balanced", expires_in: int = 3,
                 extractor=None, claimant_kinds: Sequence[str] = ("honest", "inflator")) -> None:
        self.board = board
        #: agent kinds that pursue bounties. X19 (P5) used honest + inflator only; P5b adds "farmer": a farmer
        #: runs no solve cycle, so once per epoch it claims the head of its ranking and self-reports completion
        #: with the capsule asset_id of a bundle it has just published (nothing checks it, as ``complete``).
        self.claimant_kinds = tuple(claimant_kinds)
        self.per_epoch, self.amount, self.poster = per_epoch, float(amount), poster
        self.strategy, self.expires_in = strategy, expires_in
        self.extractor = extractor
        self.active: dict[str, str] = {}             # agent -> task_id
        self.log: list[dict] = []

    def on_epoch_start(self, sim, ep: int, rng) -> None:
        self.board.expire(ep)
        for a, tid in list(self.active.items()):
            if self.board.tasks[tid].status != "claimed":
                del self.active[a]
        from .signals import TaskSignalExtractor
        ext = self.extractor or TaskSignalExtractor()
        for _ in range(self.per_epoch):
            t = rng.choice(sim.tasks)
            self.board.post(self.poster, self.amount, ext.task_signals(t), title=f"Fix {t.family}", epoch=ep,
                            expires_in=self.expires_in, family=t.family)

    @staticmethod
    def memory_events(ag) -> list[dict]:
        return [{"signals": list(e.signals), "status": (e.outcome or {}).get("status")}
                for e in ag.store.recent_events(HISTORY_WINDOW)]

    def task_for(self, sim, sp, ag, rng):
        tid = self.active.get(sp.name)
        if tid is None:
            ranked = rank_tasks(self.board.tasks.values(), sp.name, self.memory_events(ag), self.strategy)
            for e in ranked:
                if e["reason"] == "resume" or self.board.claim(e["task"].task_id, sp.name):
                    tid = e["task"].task_id
                    self.active[sp.name] = tid
                    break
        if tid is None:
            return None
        fam = self.board.tasks[tid].meta.get("family")
        pool = [t for t in sim.tasks if t.family == fam]
        return rng.choice(pool) if pool else None

    def farmer_turn(self, sim, sp, asset_ids: Sequence[str], ep: int) -> bool:
        """P5b: a publishing-only agent claims one bounty and completes it with one of its own fresh asset ids."""
        ids = [a for a in asset_ids if a]
        if not ids:
            return False
        ranked = rank_tasks(self.board.tasks.values(), sp.name, [], self.strategy)
        for e in ranked:
            tid = e["task"].task_id
            if self.board.claim(tid, sp.name):
                if self.board.complete(tid, sp.name, ids[-1], ep):
                    self.log.append({"epoch": ep, "agent": sp.name, "task": tid, "asset": ids[-1], "farmer": True})
                    return True
                return False
        return False

    def after_cycle(self, sim, sp, ag, cr, ep: int) -> None:
        tid = self.active.get(sp.name)
        if tid is None or not cr.solidified or not ag.store.events:
            return
        ev = ag.store.events[-1]
        cap = ag.store.capsules.get(ev.capsule_id) if ev.capsule_id else None
        if cap is not None and self.board.complete(tid, sp.name, cap.asset_id or cap.id, ep):
            self.log.append({"epoch": ep, "agent": sp.name, "task": tid, "asset": cap.asset_id})
            del self.active[sp.name]
