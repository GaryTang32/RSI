"""Tasks and split discipline.

The single most repeated lesson across the techniques is *which data may make
decisions*. A :class:`TaskSuite` therefore carries named splits with fixed roles:

=============  ==============================================================
``evolve``     what the loop practises on and uses to judge its own changes
               (RRSI "evolve set", GEPA D_train, autoresearch val shard)
``val``        optional selection split (GEPA D_pareto, Meta-Harness search set)
``holdout``    same kind of task, never used for any decision (RRSI "held out")
``ood``        different kinds of task - the "never seen" benchmarks
``test``       sealed; only read by final reports
=============  ==============================================================

:meth:`TaskSuite.split` enforces the discipline at runtime: a loop that tries to
read a sealed split before ``unseal()`` raises :class:`SealedSplitError`.
"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

DECISION_SPLITS = ("evolve", "val", "train")
SEALED_SPLITS = ("holdout", "ood", "test")


@dataclass(frozen=True)
class Task:
    id: str
    input: Any
    target: Any = None
    family: str = "default"
    meta: dict = field(default_factory=dict, hash=False, compare=False)

    def to_json(self) -> dict:
        return {"id": self.id, "input": self.input, "target": self.target, "family": self.family, "meta": self.meta}

    @classmethod
    def from_json(cls, d: dict) -> "Task":
        return cls(d["id"], d.get("input"), d.get("target"), d.get("family", "default"), d.get("meta", {}))


class SealedSplitError(RuntimeError):
    pass


class TaskSuite:
    def __init__(self, tasks: Iterable[Task], splits: dict[str, list[str]], name: str = "suite") -> None:
        self.name = name
        self.tasks: dict[str, Task] = {t.id: t for t in tasks}
        self.splits = {k: list(v) for k, v in splits.items()}
        for k, ids in self.splits.items():
            missing = [i for i in ids if i not in self.tasks]
            if missing:
                raise KeyError(f"split {k!r} references unknown tasks {missing[:5]}")
        self._sealed = {s for s in SEALED_SPLITS if s in self.splits}

    # ---- access
    def split(self, name: str, *, allow_sealed: bool = False) -> list[Task]:
        if name in self._sealed and not allow_sealed:
            raise SealedSplitError(
                f"split {name!r} is sealed: it must not influence decisions. "
                "Call suite.unseal(name) only in final reporting code.")
        return [self.tasks[i] for i in self.splits.get(name, [])]

    def unseal(self, *names: str) -> None:
        for n in names or tuple(self._sealed):
            self._sealed.discard(n)

    def seal(self, *names: str) -> None:
        for n in names:
            if n in self.splits:
                self._sealed.add(n)

    def is_sealed(self, name: str) -> bool:
        return name in self._sealed

    def get(self, task_id: str) -> Task:
        return self.tasks[task_id]

    def families(self, split: Optional[str] = None) -> list[str]:
        ts = self.tasks.values() if split is None else [self.tasks[i] for i in self.splits.get(split, [])]
        return sorted({t.family for t in ts})

    def __len__(self) -> int:
        return len(self.tasks)

    def summary(self) -> dict:
        return {k: {"n": len(v), "families": sorted({self.tasks[i].family for i in v})} for k, v in self.splits.items()}

    # ---- persistence
    def to_jsonl(self, path: str | Path) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("w") as f:
            f.write(json.dumps({"__suite__": self.name, "splits": self.splits}) + "\n")
            for t in self.tasks.values():
                f.write(json.dumps(t.to_json()) + "\n")

    @classmethod
    def from_jsonl(cls, path: str | Path) -> "TaskSuite":
        lines = Path(path).read_text().splitlines()
        head = json.loads(lines[0])
        tasks = [Task.from_json(json.loads(l)) for l in lines[1:] if l.strip()]
        return cls(tasks, head["splits"], head.get("__suite__", "suite"))

    @classmethod
    def from_tasks(
        cls,
        tasks: list[Task],
        *,
        fractions: dict[str, float],
        seed: int = 0,
        by_family: Optional[dict[str, list[str]]] = None,
        name: str = "suite",
    ) -> "TaskSuite":
        """Build splits randomly (``fractions`` over tasks) or by family
        (``by_family={"ood": ["dates", "units"]}`` puts whole families in a split;
        remaining tasks are divided by ``fractions``)."""
        rng = random.Random(seed)
        splits: dict[str, list[str]] = {}
        pool = list(tasks)
        if by_family:
            for split, fams in by_family.items():
                sel = [t for t in pool if t.family in fams]
                splits.setdefault(split, []).extend(t.id for t in sel)
                pool = [t for t in pool if t.family not in fams]
        rng.shuffle(pool)
        total = sum(fractions.values())
        if fractions and total <= 0:
            raise ValueError(f"fractions must sum to a positive number, got {fractions}")
        start = 0
        items = list(fractions.items())
        for j, (split, frac) in enumerate(items):
            n = len(pool) - start if j == len(items) - 1 else int(round(len(pool) * frac / total))
            splits.setdefault(split, []).extend(t.id for t in pool[start:start + n])
            start += n
        return cls(tasks, splits, name)
