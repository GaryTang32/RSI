"""Experiment ledger (every attempt as a node in a tree, append-only JSONL) and a content-addressed artifact store."""
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
    change: str = ""
    artifact_id: Optional[str] = None
    diff: str = ""
    metrics: dict = field(default_factory=dict)
    meta: dict = field(default_factory=dict)
    t: float = field(default_factory=time.time)
    seq: int = 0                     # creation order

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, d: dict) -> "Node":
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


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
        if not (self.path and self.path.exists()):
            return
        for line in self.path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                n = Node.from_json(json.loads(line))
            except (ValueError, TypeError, KeyError):   # a line torn by a crash mid-write
                self.n_corrupt_lines += 1
                continue
            if n.id not in self._nodes:
                self._order.append(n.id)
            self._nodes[n.id] = n  # later lines update earlier ones

    def add(self, node: Node) -> Node:
        """Append a node; re-adding an existing id replaces it but keeps its ``seq``."""
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
        """Change fields and append the new version; ``metrics``/``meta`` merge, unknown names go to ``meta``."""
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
        if not self.path:
            return
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

    def __bool__(self) -> bool:
        return True   # `ledger or Ledger()` must keep an empty ledger

    def __len__(self) -> int:
        return len(self._order)

    def __getitem__(self, node_id: str) -> Node:
        return self._nodes[node_id]

    def nodes(self, kind: Optional[str] = None, status: Optional[str] = None) -> list[Node]:
        return [n for n in (self._nodes[i] for i in self._order)
                if (not kind or n.kind == kind) and (not status or n.status == status)]

    def children(self, node_id: Optional[str]) -> list[Node]:
        return [n for n in self.nodes() if n.parent == node_id]

    def lineage(self, node_id: str) -> list[Node]:
        """Root-first ancestors ending at ``node_id``; stops at an unknown parent or a cycle."""
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
        return out[::-1]

    def best(self, lower_is_better: bool = False, status: Optional[str] = None) -> Optional[Node]:
        cands = [n for n in self.nodes(status=status) if n.score is not None]
        return (min if lower_is_better else max)(cands, key=lambda n: n.score) if cands else None

    def render(self, last: int = 40, fields: Iterable[str] = ("round", "status", "score", "cost", "change")) -> str:
        """Compact text table of recent attempts (fed back to proposers)."""
        def cell(v: Any) -> str:
            if isinstance(v, float):
                v = f"{v:.4f}"
            return str(v)[:160] if v is not None else "-"

        rows = [" | ".join(cell(getattr(n, f, None)) for f in fields) for n in self.nodes()[-last:]]
        return " | ".join(fields) + "\n" + "\n".join(rows)

    def to_results_tsv(self, path: str | Path, score_fmt: str = "{:.6f}") -> None:
        """autoresearch-style results table: commit, score, memory_gb, status, description."""
        with Path(path).open("w", newline="") as f:
            w = csv.writer(f, delimiter="\t")
            w.writerow(["commit", "score", "memory_gb", "status", "description"])
            for n in self.nodes():
                sc = score_fmt.format(n.score if n.score is not None else 0.0)
                mem = n.metrics.get("memory_gb") or 0.0
                desc = " ".join(str(n.change or "").split())  # one line: no tabs or newlines
                w.writerow([(n.artifact_id or n.id)[:7], sc, f"{float(mem):.1f}", n.status, desc[:200]])

    def to_json(self) -> list[dict]:
        return [n.to_json() for n in self.nodes()]


class ArtifactStore:
    """Content-addressed artifacts on disk at ``<root>/<id[:2]>/<id>.json``."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, artifact_id: str) -> Path:
        return self.root / artifact_id[:2] / f"{artifact_id}.json"

    def put(self, artifact: Artifact) -> str:
        p = self._path(artifact.id)
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_name(f"{p.name}.{uuid.uuid4().hex[:8]}.tmp")
            tmp.write_text(json.dumps(artifact.to_json()))
            tmp.replace(p)  # atomic: concurrent puts of one id never expose a partial file
        return artifact.id

    def get(self, artifact_id: str) -> Artifact:
        return Artifact.from_json(json.loads(self._path(artifact_id).read_text()))

    def has(self, artifact_id: str) -> bool:
        return self._path(artifact_id).exists()
