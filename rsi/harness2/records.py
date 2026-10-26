"""Per-task records: rollouts, improver stages and the TaskRecord that ``run`` returns (``res.records``)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ..core.artifact import Artifact
from ..core.domain import Execution

EDITOR_ROLES = ("inv", "dom", "composer")


@dataclass
class Rollout:
    """One ungraded execution of a harness on the (open) task; it never carries a score."""

    label: str
    tree_id: str
    seed: int
    execution: Execution

    @property
    def ok(self) -> bool:
        return self.execution.error is None

    @property
    def delivered(self) -> bool:
        return self.ok and bool(str(self.execution.output or "").strip())

    def to_json(self, max_trace: int = 4000) -> dict:
        ex = self.execution
        trace = ex.trace if len(ex.trace or "") <= max_trace else ex.trace[:max_trace] + "...[truncated]"
        return {"label": self.label, "tree": self.tree_id[:10], "seed": self.seed, "ok": self.ok,
                "delivered": self.delivered, "output": str(ex.output or "")[:max_trace], "error": ex.error,
                "steps": ex.steps, "tokens": ex.tokens, "trace": trace}


@dataclass
class Stage:
    """One improver session (editor, composer, selector or reflector) and what it decided."""

    label: str
    role: str
    tree: Optional[Artifact] = None
    kept_base: bool = False
    fell_back: bool = False
    adopted_from: Optional[str] = None
    error: Optional[str] = None
    notes: str = ""
    attempts: int = 0
    prompt: str = ""
    reply: str = ""

    def to_json(self) -> dict:
        return {"label": self.label, "role": self.role, "tree": self.tree.short_id if self.tree else None,
                "kept_base": self.kept_base, "fell_back": self.fell_back, "adopted_from": self.adopted_from,
                "error": self.error, "notes": self.notes[:4000], "attempts": self.attempts}


@dataclass
class TaskRecord:
    task_id: str
    domain_key: str
    mode: str
    k: int
    base_tree: Artifact
    status: str = "running"                 # ok | partial | base-failed
    adopted: Optional[str] = None           # label of the submitted final (H*_t)
    selection: dict = field(default_factory=dict)
    stages: list[Stage] = field(default_factory=list)
    rollouts: dict[str, Rollout] = field(default_factory=dict)
    trees: dict[str, Artifact] = field(default_factory=dict)
    reflection: dict = field(default_factory=dict)
    library_added: Optional[str] = None
    notes: list[str] = field(default_factory=list)

    @property
    def base_rollout(self) -> Optional[Rollout]:
        return self.rollouts.get("base1")

    @property
    def submitted(self) -> Optional[Rollout]:
        """The re-execution of the composed/selected harness: the task's deliverable."""
        return self.rollouts.get(self.adopted) if self.adopted else None

    @property
    def adopted_tree(self) -> Optional[Artifact]:
        return self.trees.get(self.adopted) if self.adopted else None

    def kept_base(self) -> bool:
        """True when the submitted harness is the unmodified base harness."""
        return self.adopted_tree is None or self.adopted_tree == self.base_tree

    def counts(self) -> dict:
        roles = [s.role for s in self.stages]
        return {"rollouts": len(self.rollouts), "sessions": sum(r in EDITOR_ROLES for r in roles),
                "selector": roles.count("selector"), "reflector": roles.count("reflector"),
                "attempts": sum(s.attempts for s in self.stages if s.role in EDITOR_ROLES),
                "fell_back": sum(s.fell_back for s in self.stages), "kept_base": sum(s.kept_base for s in self.stages)}

    def to_json(self) -> dict:
        return {"task_id": self.task_id, "domain": self.domain_key, "mode": self.mode, "k": self.k,
                "base_tree": self.base_tree.short_id, "status": self.status, "adopted": self.adopted,
                "adopted_tree": self.adopted_tree.short_id if self.adopted_tree else None,
                "kept_base": self.kept_base(), "selection": self.selection, "counts": self.counts(),
                "stages": [s.to_json() for s in self.stages],
                "rollouts": {k: r.to_json() for k, r in self.rollouts.items()},
                "reflection": self.reflection, "library_added": self.library_added, "notes": self.notes}
