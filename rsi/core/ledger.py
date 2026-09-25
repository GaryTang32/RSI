"""Experiment ledger: every attempt as a node in a tree.

"Log experiments as a tree. Record each attempt's parent, change, score and cost.
That history becomes a simulator for testing better strategies later."

One :class:`Ledger` serves all methods:

* autoresearch - a single chain; ``to_results_tsv`` writes the 5-column table
  (commit, score, memory, status, description);
* RRSI - per-edit records (component, hypothesis, dS, dC, verdict) read back as
  the proposer's memory of what failed;
* Meta-Harness - the full-history filesystem (``ArtifactStore`` + traces);
* Dream-RSI - :meth:`Ledger.nodes` exported as a replay world.

Storage is append-only JSONL, safe to resume after a crash.
"""
from __future__ import annotations

import csv
import json
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Iterable, Optional

from .artifact import Artifact


@dataclass
class Node:
    id: str
    parent: Optional[str]
    round: int = 0
    kind: str = "candidate"          # baseline | candidate | merge | policy | gene | ...
    status: str = "pending"          # keep | discard | crash | rejected | accepted | lost | ...
    score: Optional[float] = None
    cost: Optional[float] = None
    change: str = ""                 # one-line description / hypothesis
    artifact_id: Optional[str] = None
    diff: str = ""
    metrics: dict = field(default_factory=dict)
    meta: dict = field(default_factory=dict)
    t: float = field(default_factory=time.time)
    seq: int = 0                     # creation order (Dream-RSI replay uses it)

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, d: dict) -> "Node":
        known = {k: d[k] for k in cls.__dataclass_fields__ if k in d}
        return cls(**known)


def new_id(prefix: str = "n") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


class Ledger:
    def __init__(self, path: Optional[str | Path] = None) -> None:
        self.path = Path(path) if path else None
        self._nodes: dict[str, Node] = {}
        self._order: list[str] = []
        self._lock = threading.Lock()
        self.n_corrupt_lines = 0
        self._tail_checked = False
        if self.path and self.path.exists():
            text = self.path.read_text()
            for line in text.splitlines():
                if line.strip():
                    try:
                        n = Node.from_json(json.loads(line))
                    except (ValueError, TypeError, KeyError):
                        # a line torn by a crash mid-write: skip it (the append-only log stays usable)
                        self.n_corrupt_lines += 1
                        continue
                    if n.id not in self._nodes:
                        self._order.append(n.id)
                    self._nodes[n.id] = n  # later lines update earlier ones

    # ---- writing
    def add(self, node: Node) -> Node:
        """Append a node. Re-adding an existing id replaces it but keeps its
        creation order (``seq``)."""
        with self._lock:
            if node.id not in self._nodes:
                node.seq = len(self._order)
                self._order.append(node.id)
            else:
                node.seq = self._nodes[node.id].seq
            self._nodes[node.id] = node
            self._write(node)
        return node

    def update(self, node_id: str, **fields: Any) -> Node:
        """Change fields of a node and append the new version. ``metrics`` and
        ``meta`` dicts are merged; keyword names that are not :class:`Node`
        fields are stored in ``meta`` so they survive a resume."""
        with self._lock:
            n = self._nodes[node_id]
            for k, v in fields.items():
                if k in ("metrics", "meta") and isinstance(v, dict):
                    getattr(n, k).update(v)
                elif k in Node.__dataclass_fields__:
                    setattr(n, k, v)
                else:
                    n.meta[k] = v
            self._write(n)
            return n

    def _write(self, node: Node) -> None:
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(node.to_json(), default=str) + "\n"
            if not self._tail_checked:
                self._tail_checked = True
                if self.path.exists() and self.path.stat().st_size:
                    with self.path.open("rb") as f:
                        f.seek(-1, 2)
                        if f.read(1) != b"\n":  # a torn last line from a crash: start on a fresh line
                            line = "\n" + line
            with self.path.open("a") as f:
                f.write(line)

    # ---- reading
    def __len__(self) -> int:
        return len(self._order)

    def __getitem__(self, node_id: str) -> Node:
        return self._nodes[node_id]

    def nodes(self, kind: Optional[str] = None, status: Optional[str] = None) -> list[Node]:
        out = [self._nodes[i] for i in self._order]
        if kind:
            out = [n for n in out if n.kind == kind]
        if status:
            out = [n for n in out if n.status == status]
        return out

    def children(self, node_id: Optional[str]) -> list[Node]:
        return [n for n in self.nodes() if n.parent == node_id]

    def lineage(self, node_id: str) -> list[Node]:
        """Root-first chain of ancestors ending at ``node_id``. Stops at a parent
        id that is not in this ledger (and at cycles)."""
        out = []
        seen = set()
        cur: Optional[str] = node_id
        while cur is not None and cur not in seen:
            n = self._nodes.get(cur)
            if n is None:
                if not out:
                    raise KeyError(cur)
                break
            seen.add(cur)
            out.append(n)
            cur = n.parent
        return list(reversed(out))

    def best(self, lower_is_better: bool = False, status: Optional[str] = None) -> Optional[Node]:
        cands = [n for n in self.nodes(status=status) if n.score is not None]
        if not cands:
            return None
        return (min if lower_is_better else max)(cands, key=lambda n: n.score)

    def render(self, last: int = 40, fields: Iterable[str] = ("round", "status", "score", "cost", "change")) -> str:
        """Compact text table of recent attempts (fed back to proposers)."""
        rows = []
        for n in self.nodes()[-last:]:
            vals = []
            for f in fields:
                v = getattr(n, f, None)
                if isinstance(v, float):
                    v = f"{v:.4f}"
                vals.append(str(v)[:160] if v is not None else "-")
            rows.append(" | ".join(vals))
        return " | ".join(fields) + "\n" + "\n".join(rows)

    # ---- exports
    def to_results_tsv(self, path: str | Path, score_fmt: str = "{:.6f}") -> None:
        """autoresearch-style results table: commit, score, memory_gb, status, description."""
        with Path(path).open("w", newline="") as f:
            w = csv.writer(f, delimiter="\t")
            w.writerow(["commit", "score", "memory_gb", "status", "description"])
            for n in self.nodes():
                sc = score_fmt.format(n.score) if n.score is not None else score_fmt.format(0.0)
                mem = n.metrics.get("memory_gb") or 0.0
                desc = " ".join(str(n.change or "").split())  # one line: no tabs or newlines
                w.writerow([(n.artifact_id or n.id)[:7], sc, f"{float(mem):.1f}", n.status, desc[:200]])

    def to_json(self) -> list[dict]:
        return [n.to_json() for n in self.nodes()]


class ArtifactStore:
    """Content-addressed artifact storage on disk (``<root>/<id[:2]>/<id>.json``),
    optionally also materialized as directories for agent inspection
    (Meta-Harness's "filesystem holding every earlier candidate")."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, artifact: Artifact) -> str:
        p = self.root / artifact.id[:2] / f"{artifact.id}.json"
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_name(f"{p.name}.{uuid.uuid4().hex[:8]}.tmp")
            tmp.write_text(json.dumps(artifact.to_json()))
            tmp.replace(p)  # atomic: concurrent puts of one id never expose a partial file
        return artifact.id

    def get(self, artifact_id: str) -> Artifact:
        p = self.root / artifact_id[:2] / f"{artifact_id}.json"
        return Artifact.from_json(json.loads(p.read_text()))

    def has(self, artifact_id: str) -> bool:
        return (self.root / artifact_id[:2] / f"{artifact_id}.json").exists()
