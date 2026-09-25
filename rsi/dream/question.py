"""The shared decision interface: one Question API, two transitions.

"Both the online and offline phases use this same decision interface but differ
in the transition that follows a selected batch" [paper:§3 p.5].

* :class:`GridQuestion` - the common branch x attempt bookkeeping: legal roots /
  frontiers inside the plan grid, batch legality (distinct, legal before the call,
  at most ``W`` cells, never a parent with its child), the round counter ``k``
  (every non-empty batch is one round), ``N`` (revealed non-root nodes), per-round
  batch sizes and a per-round log (prefix state, batch, revealed outcomes) that
  becomes ``policy_execution_traces.jsonl``.
* :class:`ReplayQuestion` - deterministic reveal over a frozen :class:`DiscoveryTree`:
  ``root_mode="earliest"`` (paper §3: picking a root opens the earliest-created
  unrevealed branch) or ``"addressable"`` (Listing-2 API: a specific root cell);
  picking a leaf reveals its recorded child; a missing continuation reveals nothing
  (the leaf is then exhausted); the plan is intersected with the trace's support.
* :class:`OnlineQuestion` - the stochastic, costly transition: every selected cell
  runs the frozen discovery agent from the parent's saved workspace and the locked
  evaluator, in a pool of ``workers`` threads, appending new nodes to a live tree.

Policies never receive these objects directly: :mod:`rsi.dream.guard` wraps them.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional, Sequence

from ..core.artifact import Artifact
from .policy_api import BatchError, CellMeta, GridPlan, Observation
from .tree import ROOT_ID, DiscoveryNode, DiscoveryTree, cell_id, parse_cell


class GridQuestion:
    """Branch x attempt bookkeeping shared by replay and online questions."""

    def __init__(self, W: int, plan: GridPlan, *, K: Optional[int] = None) -> None:
        if W < 1:
            raise ValueError("W must be >= 1")
        self.max_parallelism = int(W)
        self.plan = GridPlan(max(0, int(plan.branch_count)), max(0, int(plan.refine_count)), plan.reason)
        self.K = K
        self._reset_state()

    # ------------------------------------------------------------ to specialise
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

    # ------------------------------------------------------------------- state
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
        """Start the episode over ("replay resets the policy's per-rollout state")."""
        self._reset_state()

    @property
    def baseline_score(self) -> Optional[float]:
        raise NotImplementedError

    # ------------------------------------------------------------------- views
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
        out = []
        for b, a in sorted(self._last.items()):
            if b in self._exhausted or a >= self.plan.refine_count:
                continue
            if not self._frontier_exists(b, a + 1):
                continue
            out.append(cell_id(b, a + 1))
        return out

    def legal_actions(self) -> list[str]:
        return self.legal_roots() + self.legal_frontiers()

    def opened_branches(self) -> list[int]:
        return sorted(self._last)

    def meta(self, cid: str) -> CellMeta:
        b, a = parse_cell(cid)
        m = self._meta(b, a)
        o = self._revealed.get(cid)
        if o is not None:
            m.parent_id = o.parent_id
        return m

    def is_done(self) -> bool:
        return self._done

    def _scaffold_budget_done(self, budget: Optional[int]) -> bool:
        return self._done or (budget is not None and len(self._revealed) >= int(budget))

    # bookkeeping only - the guard forbids policies from reading these
    @property
    def best_so_far(self) -> Optional[float]:
        vals = [o.score for o in self._revealed.values() if o.success and o.score is not None]
        b = self.baseline_score
        if b is not None:
            vals.append(b)
        return max(vals) if vals else None

    @property
    def budget_spent(self) -> int:
        return len(self._revealed)

    @property
    def N(self) -> int:
        return len(self._revealed)

    # ------------------------------------------------------------------ probing
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
        per_branch: dict[int, int] = {}
        for c in cells:
            b, a = parse_cell(c)
            per_branch[b] = per_branch.get(b, 0) + 1
        if any(v > 1 for v in per_branch.values()):
            return "batch contains a parent and its child (more than one cell of one branch)"
        return None

    def _prefix_summary(self, max_per_branch: int = 4) -> dict:
        br: dict[int, list] = {}
        for o in sorted(self._revealed.values(), key=lambda o: (o.branch, o.attempt)):
            br.setdefault(o.branch, []).append(round(o.score, 6) if o.success and o.score is not None
                                               else o.fail_class)
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
                b, _ = parse_cell(req)
                self._exhausted.add(b)
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
        """Authoritative episode statistics (computed parent-side, never trusted from the policy)."""
        succ = [o.score for o in self._revealed.values() if o.success and o.score is not None]
        base = self.baseline_score
        best = max(succ + ([base] if base is not None else [])) if (succ or base is not None) else None
        curve, n, cur = [], 0, base
        for row in self.round_log:
            n += len(row["revealed"])
            for r in row["revealed"]:
                if r["fail_class"] == "ok" and r["score"] is not None:
                    cur = r["score"] if cur is None else max(cur, r["score"])
            curve.append([n, cur])
        return {"N": len(self._revealed), "k": self.k, "batch_sizes": list(self.batch_sizes), "best": best,
                "baseline": base, "curve": curve, "revealed": sorted(self._revealed, key=lambda c: self.reveal_round[c]),
                "reveal_round": dict(self.reveal_round), "plan": self.plan.to_dict(),
                "violations": list(self.violations), "batch_errors": list(self.batch_errors)}


# =============================================================================== replay
class ReplayQuestion(GridQuestion):
    """Deterministic replay over one recorded tree [paper:§3 p.5-6; App.B.2].

    Parameters
    ----------
    tree: the frozen world ``T_i``.
    W: max batch size (``question.max_parallelism``).
    plan: the policy's :class:`GridPlan` (None = the trace's full support). Cells beyond
        ``plan ∩ support`` are never legal; ``out_of_support`` records whether the plan
        asked for more than the trace holds (Listing 2: such plans "cannot earn replay reward").
    K: replay round limit ``K2`` (None = unlimited; the paper replays with ``budget=None``).
    root_mode: ``"earliest"`` (paper formalism) or ``"addressable"`` (Listing-2 API).
    hide_missing: if True, frontier cells the trace does not contain are never legal
        (the API's "irregular grid"); if False (paper formalism), selecting such a leaf
        is legal, reveals nothing, counts toward ``k`` and exhausts the branch.
    """

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
        if first is None:          # a planned root the recording never opened: missing continuation
            return not self.hide_missing
        return first.parent_id == ROOT_ID or first.parent_id in self._revealed

    def _frontier_exists(self, branch: int, attempt: int) -> bool:
        return (not self.hide_missing) or self.tree.cell(branch, attempt) is not None

    def _meta(self, branch: int, attempt: int) -> CellMeta:
        show_tags = self.root_mode == "addressable" or attempt > 0
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
                nxt = next((x for x in pending_roots if x not in used), None)
                if nxt is None:
                    out.append(None)
                    continue
                used.add(nxt)
                b = nxt
            node = self.tree.cell(b, a)
            out.append(self.tree.observation(node) if node is not None else None)
        return out


# =============================================================================== online
def _seed_of(*parts) -> int:
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:8], 16)


class OnlineQuestion(GridQuestion):
    """The live, stochastic transition [paper:§3.3].

    Every selected cell starts one generation-evaluation request: the frozen
    ``agent`` resumes the parent's saved workspace (root = the round's initial
    workspace) and writes a new candidate; the locked ``task`` evaluator scores it;
    a :class:`DiscoveryNode` with the snapshot, proposal, diagnostics and cost is
    appended to ``self.tree``. Requests of one batch run concurrently on
    ``workers`` threads (one worker per request); node ``seq`` follows batch order,
    so replaying the recording policy reproduces the rollout exactly.

    ``context_fn(parent_node, branch, attempt) -> AttemptContext`` builds what the
    agent reads (sibling attempts, history ``H_{t-1}``, baseline, direction guidance);
    ``call_budget`` caps agent calls (the batch is truncated and the episode ends).
    """

    def __init__(self, *, task, agent, root_artifact: Artifact, root_eval, W: int, plan: GridPlan,
                 K: Optional[int] = None, workers: Optional[int] = None, store=None, meter=None,
                 context_fn: Optional[Callable] = None, directions: Optional[dict] = None, seed: int = 0,
                 round_index: int = 1, world_id: str = "live", call_budget: Optional[int] = None,
                 program_filter: Optional[Callable[[Artifact], Artifact]] = None) -> None:
        self.task, self.agent = task, agent
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
        # the live tree cannot be un-grown; reset only restarts bookkeeping at episode start
        if self.k:
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
        ctx = self.context_fn(parent, parent_ws, b, a) if self.context_fn else self._default_context(parent, parent_ws,
                                                                                                        b, a)
        seed = _seed_of(self.seed, self.round_index, b, a)
        from .agent import AgentAttempt, EvalOutcome  # local import: avoid a module cycle

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
        cost = {"calls": 1.0, "wall_s": dt, "eval_s": ev.seconds}
        if usage is not None:
            cost.update({"tokens": float(getattr(usage, "total_tokens", 0)), "usd": float(getattr(usage, "cost_usd", 0.0))})
        return DiscoveryNode(cid, parent_id, b, a, 0, ev.score, ev.evaluated, ev.valid, ev.fail_class, ev.error,
                             ev.n_valid, ev.n_total, 0, aid, att.proposal or "", dict(ev.diagnostics or {}), cost,
                             {"direction": self.directions.get(b, {}).get("direction")})

    def _default_context(self, parent: DiscoveryNode, parent_ws: Artifact, b: int, a: int):
        """What the agent reads when no ``context_fn`` is given: the problem, the parent's
        workspace, this branch's lineage, sibling attempts and the branch direction."""
        from .agent import AttemptContext, record_of

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
                # the agent-call budget cuts this batch: only the first `left` requests run, and the
                # recorded round (batch size, k) reflects what was executed; flagged in the tree meta
                err = self.validate_batch(cells)
                if err:
                    self.batch_errors.append(err)
                    raise BatchError(err)
                self.tree.meta["truncated_batch"] = {"round": self.k + 1, "requested": cells, "executed": cells[:left]}
                cells = cells[:left]
        return super().probe_batch(cells, on_reveal)


# ------------------------------------------------------------------------ workspaces
WORKSPACE_META = ("proposal.md", "eval/score.json", "error.txt")


def workspace_of(program: Artifact, proposal: str, ev) -> Artifact:
    """A node's saved workspace: the program files plus ``proposal.md``,
    ``eval/score.json`` and ``error.txt`` (App. B.1 attempt layout)."""
    score = {"score": ev.score, "valid": ev.valid, "fail_class": ev.fail_class, "n_valid": ev.n_valid,
             "n_total": ev.n_total, "diagnostics": _jsonable(ev.diagnostics or {})}
    upd = {"proposal.md": proposal or "", "eval/score.json": json.dumps(score, sort_keys=True, default=str)}
    upd["error.txt"] = ev.error if ev.error else None
    return program.with_files(upd)


def program_only(ws: Artifact) -> Artifact:
    return ws.with_files({k: None for k in WORKSPACE_META})


def _jsonable(d: dict) -> dict:
    try:
        json.dumps(d)
        return d
    except TypeError:
        return {k: (v if isinstance(v, (int, float, str, bool, type(None))) else repr(v)[:200]) for k, v in d.items()}
