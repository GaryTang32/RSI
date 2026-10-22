"""One decision interface, two transitions [paper:§3]: deterministic replay over a frozen tree, or live attempts."""
from __future__ import annotations

import hashlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional, Sequence

from ..core.artifact import Artifact
from .agent import AgentAttempt, AttemptContext, EvalOutcome, record_of
from .policy_api import BatchError, CellMeta, GridPlan, Observation
from .tree import ROOT_ID, DiscoveryNode, DiscoveryTree, cell_id, parse_cell


class GridQuestion:
    """Branch x attempt bookkeeping shared by replay and online questions; every non-empty batch is one round."""

    def __init__(self, W: int, plan: GridPlan, *, K: Optional[int] = None) -> None:
        if W < 1:
            raise ValueError("W must be >= 1")
        self.max_parallelism = int(W)
        self.plan = GridPlan(max(0, int(plan.branch_count)), max(0, int(plan.refine_count)), plan.reason)
        self.K = K
        self._reset_state()

    def _root_slots(self) -> list[int]:  # branch ids that may be opened, in legal-root order
        raise NotImplementedError

    def _root_ready(self, branch: int) -> bool:
        return True

    def _frontier_exists(self, branch: int, attempt: int) -> bool:
        return True

    def _transition(self, cells: list[str]) -> list[Optional[Observation]]:
        raise NotImplementedError

    def _meta(self, branch: int, attempt: int) -> CellMeta:
        return CellMeta(cell_id(branch, attempt), branch, attempt,
                        ROOT_ID if attempt == 0 else cell_id(branch, attempt - 1), None, {})

    def _complete(self) -> bool:  # everything that can ever be revealed has been revealed
        return False

    def _reset_state(self) -> None:
        self._revealed: dict[str, Observation] = {}
        self._last: dict[int, int] = {}
        self._exhausted: set[int] = set()
        self._done = False
        self.k = 0
        self.batch_sizes: list[int] = []
        self.round_log: list[dict] = []
        self.reveal_round: dict[str, int] = {}
        self.violations: list[str] = []
        self.batch_errors: list[str] = []

    def reset(self) -> None:
        self._reset_state()

    @property
    def baseline_score(self) -> Optional[float]:
        raise NotImplementedError

    def observed(self) -> dict[str, Observation]:
        return dict(self._revealed)

    def legal_roots(self) -> list[str]:
        if self._done:
            return []
        return [cell_id(b, 0) for b in self._root_slots()
                if b not in self._last and b not in self._exhausted and self._root_ready(b)]

    def legal_frontiers(self) -> list[str]:
        if self._done:
            return []
        return [cell_id(b, a + 1) for b, a in sorted(self._last.items())
                if b not in self._exhausted and a < self.plan.refine_count and self._frontier_exists(b, a + 1)]

    def legal_actions(self) -> list[str]:
        return self.legal_roots() + self.legal_frontiers()

    def opened_branches(self) -> list[int]:
        return sorted(self._last)

    def meta(self, cid: str) -> CellMeta:
        m = self._meta(*parse_cell(cid))
        o = self._revealed.get(cid)
        if o is not None:
            m.parent_id = o.parent_id
        return m

    def is_done(self) -> bool:
        return self._done

    def _scaffold_budget_done(self, budget: Optional[int]) -> bool:
        return self._done or (budget is not None and len(self._revealed) >= int(budget))

    # bookkeeping only: the guard forbids policies from reading these
    @property
    def best_so_far(self) -> Optional[float]:
        b = self.baseline_score
        vals = [o.score for o in self._revealed.values() if o.success and o.score is not None]
        vals += [b] if b is not None else []
        return max(vals) if vals else None

    @property
    def budget_spent(self) -> int:
        return len(self._revealed)

    @property
    def N(self) -> int:
        return len(self._revealed)

    def validate_batch(self, cells: Sequence[str]) -> Optional[str]:
        cells = list(cells)
        if not cells:
            return "empty batch (an empty batch means stop: do not call probe_batch)"
        if len(cells) > self.max_parallelism:
            return f"batch of {len(cells)} cells exceeds max_parallelism={self.max_parallelism}"
        if len(set(cells)) != len(cells):
            return "batch contains duplicate cell ids"
        legal = set(self.legal_actions())
        bad = [c for c in cells if c not in legal]
        if bad:
            return f"illegal cells {bad[:5]} (legal before the call: roots + opened-branch frontiers)"
        if len({parse_cell(c)[0] for c in cells}) != len(cells):
            return "batch contains a parent and its child (more than one cell of one branch)"
        return None

    def _prefix_summary(self, max_per_branch: int = 4) -> dict:
        br: dict[int, list] = {}
        for o in sorted(self._revealed.values(), key=lambda o: (o.branch, o.attempt)):
            br.setdefault(o.branch, []).append(round(o.score, 6) if o.success and o.score is not None else o.fail_class)
        return {"n": len(self._revealed), "branches": {str(b): v[-max_per_branch:] for b, v in br.items()}}

    def probe_batch(self, cells: Sequence[str], on_reveal: Optional[Callable[[Observation], object]] = None
                    ) -> list[Observation]:
        cells = [str(c) for c in cells]
        if self._done:
            return []
        err = self.validate_batch(cells)
        if err:
            self.batch_errors.append(err)
            raise BatchError(err)
        prefix = self._prefix_summary()
        legal_n = len(self.legal_actions())
        outs = self._transition(cells)
        self.k += 1
        self.batch_sizes.append(len(cells))
        revealed = []
        for req, o in zip(cells, outs):
            if o is None:
                self._exhausted.add(parse_cell(req)[0])
                continue
            self._revealed[o.cell_id] = o
            self._last[o.branch] = max(self._last.get(o.branch, -1), o.attempt)
            self.reveal_round[o.cell_id] = self.k
            revealed.append(o)
        self.round_log.append({
            "round": self.k, "prefix": prefix, "legal_n": legal_n, "batch": cells,
            "revealed": [{"cell": o.cell_id, "score": o.score, "fail_class": o.fail_class} for o in revealed],
            "empty": [c for c, o in zip(cells, outs) if o is None],
        })
        if (self.K is not None and self.k >= self.K) or self._complete() or not self.legal_actions():
            self._done = True
        if on_reveal is not None:
            for o in revealed:
                on_reveal(o)
        return revealed

    def stats(self) -> dict:
        """Authoritative episode statistics, computed parent-side."""
        base = self.baseline_score
        curve, n, cur = [], 0, base
        for row in self.round_log:
            n += len(row["revealed"])
            for r in row["revealed"]:
                if r["fail_class"] == "ok" and r["score"] is not None:
                    cur = r["score"] if cur is None else max(cur, r["score"])
            curve.append([n, cur])
        return {"N": len(self._revealed), "k": self.k, "batch_sizes": list(self.batch_sizes), "best": self.best_so_far,
                "baseline": base, "curve": curve,
                "revealed": sorted(self._revealed, key=lambda c: self.reveal_round[c]),
                "reveal_round": dict(self.reveal_round), "plan": self.plan.to_dict(),
                "violations": list(self.violations), "batch_errors": list(self.batch_errors)}


class ReplayQuestion(GridQuestion):
    """Deterministic reveal over a frozen tree, restricted to ``plan ∩ support`` [paper:§3; App.B.2]."""

    def __init__(self, tree: DiscoveryTree, W: int, plan: Optional[GridPlan] = None, *, K: Optional[int] = None,
                 root_mode: str = "earliest", hide_missing: bool = False) -> None:
        if root_mode not in ("earliest", "addressable"):
            raise ValueError("root_mode must be 'earliest' or 'addressable'")
        self.tree = tree
        self.root_mode = root_mode
        self.hide_missing = hide_missing
        self.support = GridPlan(tree.trace_branch_count, tree.trace_refine_count, "trace support")
        req = plan or GridPlan(self.support.branch_count, self.support.refine_count, "full support")
        self.requested_plan = req
        self.out_of_support = (req.branch_count > self.support.branch_count or
                               req.refine_count > self.support.refine_count)
        eff = GridPlan(min(req.branch_count, self.support.branch_count),
                       min(req.refine_count, self.support.refine_count), req.reason)
        recorded = tree.branch_order() if root_mode == "earliest" else sorted(tree.branches())
        order = recorded + [b for b in range(self.support.branch_count) if b not in set(recorded)]
        self._slots = order[: eff.branch_count]
        self._in_plan = {n.id for n in tree.non_root()
                         if n.branch in set(self._slots) and n.attempt <= eff.refine_count}
        super().__init__(W, eff, K=K)

    @property
    def baseline_score(self) -> Optional[float]:
        return self.tree.root.score

    def _root_slots(self) -> list[int]:
        return list(self._slots)

    def _root_ready(self, branch: int) -> bool:
        first = self.tree.cell(branch, 0)
        if first is None:  # a planned root the recording never opened: missing continuation
            return not self.hide_missing
        return first.parent_id == ROOT_ID or first.parent_id in self._revealed

    def _frontier_exists(self, branch: int, attempt: int) -> bool:
        return (not self.hide_missing) or self.tree.cell(branch, attempt) is not None

    def _meta(self, branch: int, attempt: int) -> CellMeta:
        # in "earliest" mode an unrevealed root is only a slot, so its tags would describe the wrong branch
        show_tags = self.root_mode == "addressable" or attempt > 0 or cell_id(branch, attempt) in self._revealed
        return self.tree.cell_meta(branch, attempt, with_tags=show_tags)

    def _complete(self) -> bool:
        return self._in_plan.issubset(self._revealed)

    def _transition(self, cells: list[str]) -> list[Optional[Observation]]:
        out: list[Optional[Observation]] = []
        pending_roots = [b for b in self._slots if b not in self._last and b not in self._exhausted
                         and self.tree.cell(b, 0) is not None and self._root_ready(b)] \
            if self.root_mode == "earliest" else []
        used: set[int] = set()
        for c in cells:
            b, a = parse_cell(c)
            if a == 0 and self.root_mode == "earliest":
                # root -> the earliest-created child of r outside the revealed subtree [paper:§3 p.6]
                b = next((x for x in pending_roots if x not in used), None)
                if b is None:
                    out.append(None)
                    continue
                used.add(b)
            node = self.tree.cell(b, a)
            out.append(self.tree.observation(node) if node is not None else None)
        return out


def _seed_of(*parts) -> int:
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:8], 16)


class OnlineQuestion(GridQuestion):
    """The live transition [paper:§3.3]: each probed cell runs the frozen agent, then the locked evaluator."""

    def __init__(self, *, task, agent, root_artifact: Artifact, root_eval, W: int, plan: GridPlan,
                 K: Optional[int] = None, workers: Optional[int] = None, store=None, meter=None,
                 context_fn: Optional[Callable] = None, directions: Optional[dict] = None, seed: int = 0,
                 round_index: int = 1, world_id: str = "live", call_budget: Optional[int] = None,
                 program_filter: Optional[Callable[[Artifact], Artifact]] = None,
                 record_attempts: bool = False) -> None:
        self.task, self.agent = task, agent
        self.attempt_log: Optional[dict[str, dict]] = {} if record_attempts else None  # audit only, never read
        self.workers = int(workers or W)
        self.store, self.meter = store, meter
        self.context_fn = context_fn
        self.directions = {int(k): dict(v) for k, v in (directions or {}).items()}
        self.seed, self.round_index = seed, round_index
        self.call_budget = call_budget
        self.program_filter = program_filter or program_only
        root_ws = workspace_of(root_artifact, "(initial workspace)", root_eval)
        rid = store.put(root_ws) if store is not None else root_ws.id
        root = DiscoveryNode(ROOT_ID, None, score=root_eval.score, evaluated=root_eval.evaluated,
                             valid=root_eval.valid, fail_class=root_eval.fail_class, error=root_eval.error,
                             n_valid=root_eval.n_valid, n_total=root_eval.n_total, round=0, artifact_id=rid,
                             diagnostics=dict(root_eval.diagnostics or {}))
        self.tree = DiscoveryTree(root, branch_tags=self.directions, world_id=world_id,
                                  meta={"round": round_index, "plan": plan.to_dict(), "W": W})
        self._artifacts: dict[str, Artifact] = {ROOT_ID: root_ws}
        self._seq = 0
        self.calls = 0
        self.wall_s = 0.0
        self._lock = threading.Lock()
        super().__init__(W, plan, K=K)

    def reset(self) -> None:
        if self.k:  # the live tree cannot be un-grown
            raise RuntimeError("an online rollout cannot be reset after it has started")
        super().reset()

    @property
    def baseline_score(self) -> Optional[float]:
        return self.tree.root.score

    def _root_slots(self) -> list[int]:
        return list(range(self.plan.branch_count))

    def _meta(self, branch: int, attempt: int) -> CellMeta:
        return CellMeta(cell_id(branch, attempt), branch, attempt,
                        ROOT_ID if attempt == 0 else cell_id(branch, attempt - 1), None,
                        dict(self.directions.get(branch, {})))

    def artifact(self, node_id: str) -> Artifact:
        return self._artifacts[node_id]

    def _one(self, cid: str) -> DiscoveryNode:
        b, a = parse_cell(cid)
        parent_id = ROOT_ID if a == 0 else cell_id(b, a - 1)
        parent = self.tree.node(parent_id)
        parent_ws = self._artifacts[parent_id]
        ctx = (self.context_fn or self._default_context)(parent, parent_ws, b, a)
        seed = _seed_of(self.seed, self.round_index, b, a)
        t0 = time.time()
        try:
            att = self.agent.attempt(ctx, seed=seed)
        except Exception as e:  # noqa: BLE001 - an agent/backend crash is a failed attempt, not a policy error
            att = AgentAttempt(None, "", error=f"agent crashed: {type(e).__name__}: {e}")
            att.meta["crash"] = True
        usage = getattr(att, "usage", None)
        if self.meter is not None:
            self.meter.add_agent(usage)
        program = att.artifact if att.artifact is not None else self.program_filter(parent_ws)
        if att.artifact is None or att.error:
            ev = EvalOutcome(None, evaluated=False, valid=False,
                             fail_class="env_error" if att.meta.get("crash") else "compile_other",
                             error=f"agent: {att.error or 'no candidate produced'}")
        else:
            try:
                ev = self.task.evaluate(self.program_filter(program), seed=seed)
            except Exception as e:  # noqa: BLE001 - evaluator infrastructure failure: typed, never a success
                ev = EvalOutcome(None, evaluated=False, valid=False, fail_class="env_error",
                                 error=f"evaluator crashed: {type(e).__name__}: {e}")
        dt = time.time() - t0
        if self.meter is not None:
            self.meter.add_eval(ev.seconds)
        ws = workspace_of(self.program_filter(program), att.proposal or "", ev)
        aid = self.store.put(ws) if self.store is not None else ws.id
        with self._lock:
            self._artifacts[cid] = ws
            if self.attempt_log is not None:
                self.attempt_log[cid] = {
                    "prompt": att.meta.get("prompt", ""), "reply": att.meta.get("reply", ""),
                    "change": att.meta.get("change", ""), "hypothesis": att.meta.get("hypothesis", ""),
                    "components": att.meta.get("components", []), "blocked": att.meta.get("blocked", []),
                    "proposal": att.proposal or "", "agent_error": att.error, "seed": seed,
                    "parent_program": self.program_filter(parent_ws),
                    "program": self.program_filter(program) if att.artifact is not None else None,
                    "context": {"direction": dict(getattr(ctx, "direction", {}) or {}),
                                "lineage": len(getattr(ctx, "lineage", []) or []),
                                "siblings": len(getattr(ctx, "siblings", []) or []),
                                "history": len(getattr(ctx, "history", []) or []),
                                "parent_score": getattr(ctx, "parent_score", None),
                                "direction_guidance": getattr(ctx, "direction_guidance", "")},
                    "eval_s": ev.seconds, "wall_s": dt,
                    "usage": None if usage is None else {"tokens": int(getattr(usage, "total_tokens", 0)),
                                                         "usd": float(getattr(usage, "cost_usd", 0.0))}}
        cost = {"calls": 1.0, "wall_s": dt, "eval_s": ev.seconds}
        if usage is not None:
            cost.update({"tokens": float(getattr(usage, "total_tokens", 0)),
                         "usd": float(getattr(usage, "cost_usd", 0.0))})
        return DiscoveryNode(cid, parent_id, b, a, 0, ev.score, ev.evaluated, ev.valid, ev.fail_class, ev.error,
                             ev.n_valid, ev.n_total, 0, aid, att.proposal or "", dict(ev.diagnostics or {}), cost,
                             {"direction": self.directions.get(b, {}).get("direction")})

    def _default_context(self, parent: DiscoveryNode, parent_ws: Artifact, b: int, a: int) -> AttemptContext:
        """Problem, parent workspace, branch lineage, sibling attempts and the branch direction."""
        d = dict(self.directions.get(b, {}))
        return AttemptContext(self.task.describe(), parent.id, parent_ws, parent.score, b, a, self.round_index,
                              [record_of(n) for n in self.tree.nodes() if n.branch == b and n.attempt < a],
                              [record_of(n) for n in self.tree.non_root() if n.branch != b], [],
                              self.tree.root.score, d,
                              f"Direction assigned to this branch: {d['direction']}." if d.get("direction") else "",
                              self.task.editable() if hasattr(self.task, "editable") else None)

    def _transition(self, cells: list[str]) -> list[Optional[Observation]]:
        t0 = time.time()
        if len(cells) <= 1 or self.workers <= 1:
            nodes = [self._one(c) for c in cells]
        else:
            with ThreadPoolExecutor(max_workers=min(self.workers, len(cells))) as ex:
                nodes = list(ex.map(self._one, cells))
        self.wall_s += time.time() - t0
        self.calls += len(nodes)
        out: list[Optional[Observation]] = []
        for n in nodes:
            self._seq += 1
            n.seq = self._seq
            n.round = self.k + 1
            self.tree.add(n)
            out.append(self.tree.observation(n))
        return out

    def _complete(self) -> bool:
        return self.call_budget is not None and self.calls >= self.call_budget

    def probe_batch(self, cells, on_reveal=None):
        if self.call_budget is not None:
            left = self.call_budget - self.calls
            if left <= 0:
                self._done = True
                return []
            cells = [str(c) for c in cells]
            if len(cells) > left:
                # only the first `left` requests run; the recorded round reflects what was executed
                err = self.validate_batch(cells)
                if err:
                    self.batch_errors.append(err)
                    raise BatchError(err)
                self.tree.meta["truncated_batch"] = {"round": self.k + 1, "requested": cells, "executed": cells[:left]}
                cells = cells[:left]
        return super().probe_batch(cells, on_reveal)


WORKSPACE_META = ("proposal.md", "eval/score.json", "error.txt")


def workspace_of(program: Artifact, proposal: str, ev) -> Artifact:
    """A node's saved workspace: program files plus ``proposal.md``, ``eval/score.json``, ``error.txt`` [App. B.1]."""
    score = {"score": ev.score, "valid": ev.valid, "fail_class": ev.fail_class, "n_valid": ev.n_valid,
             "n_total": ev.n_total, "diagnostics": _jsonable(ev.diagnostics or {})}
    return program.with_files({"proposal.md": proposal or "",
                               "eval/score.json": json.dumps(score, sort_keys=True, default=str),
                               "error.txt": ev.error if ev.error else None})


def program_only(ws: Artifact) -> Artifact:
    return ws.with_files({k: None for k in WORKSPACE_META})


def _jsonable(d: dict) -> dict:
    try:
        json.dumps(d)
        return d
    except TypeError:
        return {k: (v if isinstance(v, (int, float, str, bool, type(None))) else repr(v)[:200]) for k, v in d.items()}
