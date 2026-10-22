"""Discovery trees: a root plus chains (branches) of recorded attempts, cell ``b{b}.a{a}`` [paper:§3]."""
from __future__ import annotations

import json
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

from ..core.artifact import Artifact
from ..core.ledger import ArtifactStore, Ledger, Node
from .policy_api import CellMeta, Observation

ROOT_ID = "root"


def cell_id(branch: int, attempt: int) -> str:
    return f"b{int(branch)}.a{int(attempt)}"


def parse_cell(cid: str) -> tuple[int, int]:
    b, a = cid.split(".")
    return int(b[1:]), int(a[1:])


@dataclass
class DiscoveryNode:
    id: str
    parent_id: Optional[str]
    branch: int = -1
    attempt: int = -1
    seq: int = 0
    score: Optional[float] = None
    evaluated: bool = True
    valid: bool = True
    fail_class: str = "ok"
    error: Optional[str] = None
    n_valid: Optional[int] = None
    n_total: Optional[int] = None
    round: int = 0  # online decision round that created it (1-based; root 0)
    artifact_id: Optional[str] = None
    proposal: str = ""
    diagnostics: dict = field(default_factory=dict)
    cost: dict = field(default_factory=dict)
    tags: dict = field(default_factory=dict)

    @property
    def success(self) -> bool:
        return bool(self.evaluated) and self.error is None and self.fail_class == "ok"

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, d: dict) -> "DiscoveryNode":
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


class DiscoveryTree:
    """A recorded search (= one replay world). Larger scores are better."""

    def __init__(self, root: DiscoveryNode, nodes: Iterable[DiscoveryNode] = (), *,
                 branch_tags: Optional[dict] = None, world_id: str = "world", meta: Optional[dict] = None) -> None:
        root.id = ROOT_ID
        self.root = root
        self.world_id = world_id
        self.meta = dict(meta or {})
        self.branch_tags: dict[int, dict] = {int(k): dict(v) for k, v in (branch_tags or {}).items()}
        self._nodes: dict[str, DiscoveryNode] = {ROOT_ID: root}
        for n in nodes:
            self.add(n)

    def add(self, node: DiscoveryNode) -> DiscoveryNode:
        if node.id in self._nodes:
            raise ValueError(f"duplicate node {node.id}")
        if node.parent_id not in self._nodes:
            raise KeyError(f"parent {node.parent_id} of {node.id} not in tree")
        self._nodes[node.id] = node
        return node

    def __len__(self) -> int:
        return len(self._nodes)

    def node(self, node_id: str) -> DiscoveryNode:
        return self._nodes[node_id]

    def nodes(self) -> list[DiscoveryNode]:
        return sorted(self._nodes.values(), key=lambda n: n.seq)

    def non_root(self) -> list[DiscoveryNode]:
        return [n for n in self.nodes() if n.id != ROOT_ID]

    @property
    def size(self) -> int:
        """Number of recorded attempts (non-root nodes)."""
        return len(self._nodes) - 1

    def branches(self) -> dict[int, list[DiscoveryNode]]:
        out: dict[int, list[DiscoveryNode]] = {}
        for n in self.non_root():
            out.setdefault(n.branch, []).append(n)
        return {b: sorted(cells, key=lambda n: n.attempt) for b, cells in sorted(out.items())}

    def branch_order(self) -> list[int]:
        """Branches in creation order of their first cell (the paper's 'earliest-created child of r')."""
        return [b for b, ns in sorted(self.branches().items(), key=lambda kv: kv[1][0].seq)]

    def cell(self, branch: int, attempt: int) -> Optional[DiscoveryNode]:
        return self._nodes.get(cell_id(branch, attempt))

    @property
    def trace_branch_count(self) -> int:
        """Support width: the recorded plan's branch count (incl. unopened roots), else the branch slots used."""
        bc = max((n.branch for n in self.non_root()), default=-1) + 1
        p = self.meta.get("plan")
        return max(bc, int(p["branch_count"])) if p else bc

    @property
    def trace_refine_count(self) -> int:
        rc = max((n.attempt for n in self.non_root()), default=0)
        p = self.meta.get("plan")
        return max(rc, int(p["refine_count"])) if p else rc

    @property
    def root_score(self) -> float:
        return float(self.root.score) if self.root.score is not None else 0.0

    def best_node(self) -> DiscoveryNode:
        cands = [n for n in self.nodes() if n.score is not None and (n.id == ROOT_ID or n.success)]
        return max(cands, key=lambda n: (n.score, -n.seq)) if cands else self.root

    @property
    def ceiling(self) -> float:
        """Best recorded score (incl. the root): the replay quality ceiling."""
        s = self.best_node().score
        return float(s if s is not None else self.root_score)

    def observation(self, node: DiscoveryNode) -> Observation:
        parent = self._nodes.get(node.parent_id) if node.parent_id else None
        base = self.root.score
        dp = float(node.score) - float(parent.score) \
            if node.score is not None and parent is not None and parent.score is not None else None
        db = float(node.score) - float(base) if node.score is not None and base is not None else None
        return Observation(node.id, node.branch, node.attempt, node.score, node.evaluated, node.valid,
                           node.fail_class, node.error, db, dp, node.n_valid, node.n_total, node.parent_id)

    def cell_meta(self, branch: int, attempt: int, *, with_tags: bool = True, seq: Optional[int] = None) -> CellMeta:
        n = self.cell(branch, attempt)
        parent = n.parent_id if n is not None else (ROOT_ID if attempt == 0 else cell_id(branch, attempt - 1))
        return CellMeta(cell_id(branch, attempt), branch, attempt, parent, seq,
                        dict(self.branch_tags.get(branch, {})) if with_tags else {})

    def render_grid(self, reveal_round: Optional[dict[str, int]] = None, labels: Optional[dict[int, str]] = None,
                    fmt: str = "{:.2f}") -> str:
        """Rows = branches, columns = attempts; each cell shows its score and the round it was revealed (or --)."""
        width = self.trace_refine_count + 1
        lines = ["branch   " + " ".join(f"{('attempt ' + str(a + 1)):>11}" for a in range(width))]
        for b, cells in self.branches().items():
            name = (labels or {}).get(b, f"{b}")
            row = [f"{name:<8}"]
            by_a = {c.attempt: c for c in cells}
            for a in range(width):
                c = by_a.get(a)
                if c is None:
                    row.append(f"{'':>11}")
                    continue
                s = fmt.format(c.score) if c.score is not None else "fail"
                if reveal_round is not None:
                    r = reveal_round.get(c.id)
                    s += f" {'R' + str(r) if r else '--':>3}"
                row.append(f"{s:>11}")
            lines.append(" ".join(row))
        return "\n".join(lines)

    def to_json(self) -> dict:
        return {"world_id": self.world_id, "meta": self.meta,
                "branch_tags": {str(k): v for k, v in self.branch_tags.items()},
                "root": self.root.to_json(), "nodes": [n.to_json() for n in self.non_root()]}

    @classmethod
    def from_json(cls, d: dict) -> "DiscoveryTree":
        return cls(DiscoveryNode.from_json(d["root"]), [DiscoveryNode.from_json(n) for n in d.get("nodes", [])],
                   branch_tags=d.get("branch_tags"), world_id=d.get("world_id", "world"), meta=d.get("meta"))

    def save(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.to_json(), default=str))
        tmp.replace(p)
        return p

    @classmethod
    def load(cls, path: str | Path) -> "DiscoveryTree":
        return cls.from_json(json.loads(Path(path).read_text()))

    def to_ledger(self, ledger: Optional[Ledger] = None, *, prefix: str = "", round_offset: int = 0) -> Ledger:
        """Every node as a ledger ``Node`` (kind ``attempt``; root ``baseline``)."""
        led = ledger if ledger is not None else Ledger()
        for n in self.nodes():
            is_root = n.id == ROOT_ID
            led.add(Node(id=f"{prefix}{n.id}", parent=f"{prefix}{n.parent_id}" if n.parent_id else None,
                         round=n.round + round_offset, kind="baseline" if is_root else "attempt",
                         status="ok" if n.success else (n.fail_class or "crash"), score=n.score,
                         cost=float(n.cost.get("calls", 0.0 if is_root else 1.0)),
                         change=(n.proposal or "").splitlines()[0][:200] if n.proposal else "",
                         artifact_id=n.artifact_id,
                         metrics={"branch": n.branch, "attempt": n.attempt, "seq": n.seq},
                         meta={"world": self.world_id, "fail_class": n.fail_class, "error": n.error,
                               "evaluated": n.evaluated, "valid": n.valid, "tags": n.tags,
                               "n_valid": n.n_valid, "n_total": n.n_total,
                               **({"tree_meta": _plain(self.meta),
                                   "branch_tags": {str(k): v for k, v in self.branch_tags.items()}}
                                  if is_root else {})}))
        return led

    @classmethod
    def from_ledger(cls, ledger: Ledger, *, lower_is_better: bool = False,
                    score_fn: Optional[Callable[[Node], Optional[float]]] = None,
                    world_id: str = "ledger", include: Optional[Callable[[Node], bool]] = None) -> "DiscoveryTree":
        """Any method's ledger as a replay world: first-child chains are branches, later children fork new ones."""
        nodes = [n for n in ledger.nodes() if include is None or include(n)]
        ids = {n.id for n in nodes}
        sign = -1.0 if lower_is_better else 1.0
        native = cls._from_native(nodes, world_id) if score_fn is None and not lower_is_better else None
        if native is not None:
            return native

        def sc(n: Node) -> Optional[float]:
            v = score_fn(n) if score_fn else n.score
            return None if v is None else sign * float(v)

        kids: dict[Optional[str], list[Node]] = {}
        for n in nodes:
            kids.setdefault(n.parent if n.parent in ids else None, []).append(n)
        for v in kids.values():
            v.sort(key=lambda n: n.seq)
        tops = kids.get(None, [])
        if not tops:
            raise ValueError("ledger has no nodes")
        if len(tops) == 1:
            r = tops[0]
            root = DiscoveryNode(ROOT_ID, None, score=sc(r), round=r.round, artifact_id=r.artifact_id,
                                 proposal=r.change, tags={"ledger_id": r.id})
            starts = [(ROOT_ID, c) for c in kids.get(r.id, [])]
            ledger_to_cell = {r.id: ROOT_ID}
        else:  # several parentless nodes: a synthetic root with the lowest of their scores
            vals = [sc(t) for t in tops if sc(t) is not None]
            root = DiscoveryNode(ROOT_ID, None, score=min(vals) if vals else 0.0, tags={"synthetic": True})
            starts = [(ROOT_ID, t) for t in tops]
            ledger_to_cell = {}
        tree = cls(root, world_id=world_id, meta={"source": "ledger", "lower_is_better": lower_is_better})
        seq, b, queue = 1, -1, deque(starts)
        while queue:
            parent_cell, first = queue.popleft()
            b += 1
            if parent_cell != ROOT_ID:
                tree.branch_tags[b] = {"fork_of": parent_cell}
            cur: Optional[Node] = first
            prev_cell, a = parent_cell, 0
            while cur is not None:
                s = sc(cur)
                ok_status = cur.status not in ("crash", "rejected", "error", "timeout") and s is not None
                node = DiscoveryNode(cell_id(b, a), prev_cell, b, a, seq, s, evaluated=s is not None, valid=ok_status,
                                     fail_class="ok" if ok_status else ("rejected" if s is None else "compile_other"),
                                     error=None if ok_status else f"status={cur.status}", round=cur.round,
                                     artifact_id=cur.artifact_id, proposal=cur.change or "",
                                     diagnostics={"ledger_id": cur.id, "status": cur.status},
                                     cost={"calls": 1.0}, tags={"kind": cur.kind})
                tree.add(node)
                ledger_to_cell[cur.id] = node.id
                seq += 1
                ch = kids.get(cur.id, [])
                queue.extend((node.id, extra) for extra in ch[1:])
                prev_cell, cur, a = node.id, ch[0] if ch else None, a + 1
        tree.meta["ledger_map"] = ledger_to_cell
        return tree

    @classmethod
    def _from_native(cls, nodes: list, world_id: str) -> Optional["DiscoveryTree"]:
        """Exact inverse of :meth:`to_ledger`; None when the ledger was written by another method."""
        roots = [n for n in nodes if n.kind == "baseline" and "tree_meta" in (n.meta or {})]
        if len(roots) != 1:
            return None
        r = roots[0]
        rest = [n for n in nodes if n is not r]
        if not all({"branch", "attempt"} <= set(n.metrics or {}) for n in rest):
            return None
        prefix = r.id[: -len(ROOT_ID)] if r.id.endswith(ROOT_ID) else ""
        root = DiscoveryNode(ROOT_ID, None, score=r.score, round=0, artifact_id=r.artifact_id, proposal=r.change,
                             fail_class=r.meta.get("fail_class", "ok"), error=r.meta.get("error"),
                             n_valid=r.meta.get("n_valid"), n_total=r.meta.get("n_total"))
        tree = cls(root, world_id=world_id, meta=dict(r.meta.get("tree_meta") or {}),
                   branch_tags=r.meta.get("branch_tags") or {})
        try:
            for n in sorted(rest, key=lambda n: int(n.metrics.get("seq", n.seq))):
                b, a = int(n.metrics["branch"]), int(n.metrics["attempt"])
                parent = n.parent[len(prefix):] if n.parent and n.parent.startswith(prefix) else n.parent
                tree.add(DiscoveryNode(cell_id(b, a), parent, b, a, int(n.metrics.get("seq", n.seq)), n.score,
                                       bool(n.meta.get("evaluated", True)), bool(n.meta.get("valid", True)),
                                       n.meta.get("fail_class", "ok"), n.meta.get("error"), n.meta.get("n_valid"),
                                       n.meta.get("n_total"), n.round,
                                       n.artifact_id, n.change, {}, {"calls": float(n.cost or 1.0)},
                                       dict(n.meta.get("tags") or {})))
        except (KeyError, ValueError):
            return None
        tree.meta.setdefault("source", "ledger:native")
        return tree


def _plain(d: dict) -> dict:
    try:
        return json.loads(json.dumps(d, default=str))
    except (TypeError, ValueError):
        return {}


class SnapshotStore:
    """Content-addressed workspace snapshots (in memory, plus an ``ArtifactStore`` when ``root`` is given)."""

    def __init__(self, root: Optional[str | Path] = None) -> None:
        self.store = ArtifactStore(root) if root else None
        self._mem: dict[str, Artifact] = {}

    def put(self, artifact: Artifact) -> str:
        self._mem[artifact.id] = artifact
        if self.store is not None:
            self.store.put(artifact)
        return artifact.id

    def get(self, artifact_id: str) -> Artifact:
        if artifact_id in self._mem:
            return self._mem[artifact_id]
        if self.store is None:
            raise KeyError(artifact_id)
        self._mem[artifact_id] = a = self.store.get(artifact_id)
        return a

    def has(self, artifact_id: str) -> bool:
        return artifact_id in self._mem or (self.store is not None and self.store.has(artifact_id))
