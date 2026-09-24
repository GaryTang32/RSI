"""Artifacts: the thing a self-improvement loop changes.

An :class:`Artifact` is an immutable bundle of named text components - prompt
files, harness code, a training script, a search-policy module, a gene card.
Because every method edits text, one type covers the whole family:

* autoresearch edits ``train.py``;
* RRSI / Meta-Harness / SoL-Pi edit a harness (prompts + code + tools + memory);
* GEPA edits named prompt components;
* Dream-RSI edits the exploration-policy module;
* EvoMap packages strategies as small text assets.

Artifacts are content-addressed (``artifact.id``) so ledgers, caches and replay
worlds can refer to exact versions.
"""
from __future__ import annotations

import difflib
import hashlib
import json
from pathlib import Path
from typing import Iterable, Mapping, Optional

from .llm import estimate_tokens


class Artifact(Mapping[str, str]):
    __slots__ = ("_files", "_id", "meta")

    def __init__(self, files: Mapping[str, str], meta: Optional[dict] = None) -> None:
        self._files = dict(sorted((str(k), str(v)) for k, v in files.items()))
        self._id: Optional[str] = None
        self.meta = dict(meta or {})

    # Mapping protocol
    def __getitem__(self, key: str) -> str:
        return self._files[key]

    def __iter__(self):
        return iter(self._files)

    def __len__(self) -> int:
        return len(self._files)

    def __eq__(self, other) -> bool:
        return isinstance(other, Artifact) and self._files == other._files

    def __hash__(self) -> int:
        return hash(self.id)

    def __repr__(self) -> str:
        return f"Artifact({self.short_id}, files={list(self._files)})"

    # identity
    @property
    def id(self) -> str:
        if self._id is None:
            self._id = hashlib.sha256(json.dumps(self._files, sort_keys=True).encode()).hexdigest()
        return self._id

    @property
    def short_id(self) -> str:
        return self.id[:10]

    @property
    def files(self) -> dict[str, str]:
        return dict(self._files)

    # size = the "complexity" RRSI and SoL-Pi care about
    def size_chars(self) -> int:
        return sum(len(v) for v in self._files.values())

    def size_tokens(self) -> int:
        return sum(estimate_tokens(v) for v in self._files.values())

    # edits
    def with_files(self, updates: Mapping[str, Optional[str]], **meta) -> "Artifact":
        """New artifact with ``updates`` applied; a ``None`` value deletes a file."""
        files = dict(self._files)
        for k, v in updates.items():
            if v is None:
                files.pop(k, None)
            else:
                files[k] = v
        m = dict(self.meta)
        m.update(meta)
        return Artifact(files, m)

    def changed_files(self, other: "Artifact") -> list[str]:
        names = set(self._files) | set(other._files)
        return sorted(n for n in names if self._files.get(n) != other._files.get(n))

    def diff(self, other: "Artifact", context: int = 3) -> str:
        """Unified diff from ``self`` (old) to ``other`` (new)."""
        chunks = []
        for name in self.changed_files(other):
            a = self._files.get(name, "").splitlines(keepends=True)
            b = other._files.get(name, "").splitlines(keepends=True)
            chunks.extend(difflib.unified_diff(a, b, f"a/{name}", f"b/{name}", n=context))
        return "".join(chunks)

    def diff_size(self, other: "Artifact") -> int:
        """Number of added+removed lines between two versions."""
        n = 0
        for line in self.diff(other, context=0).splitlines():
            if (line.startswith("+") or line.startswith("-")) and not line.startswith(("+++", "---")):
                n += 1
        return n

    # persistence
    @classmethod
    def from_dir(cls, path: str | Path, patterns: Iterable[str] = ("**/*",), exclude: Iterable[str] = ()) -> "Artifact":
        root = Path(path)
        files = {}
        excl = tuple(exclude) + ("__pycache__", ".git", ".pytest_cache")
        for pat in patterns:
            for p in root.glob(pat):
                if p.is_file() and not any(part in excl for part in p.relative_to(root).parts):
                    try:
                        files[str(p.relative_to(root))] = p.read_text()
                    except UnicodeDecodeError:
                        continue
        return cls(files)

    def to_dir(self, path: str | Path, clean: bool = False) -> Path:
        root = Path(path)
        if clean and root.exists():
            import shutil

            shutil.rmtree(root)
        root.mkdir(parents=True, exist_ok=True)
        for name, text in self._files.items():
            p = root / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
        return root

    def to_json(self) -> dict:
        return {"id": self.id, "files": self._files, "meta": self.meta}

    @classmethod
    def from_json(cls, d: dict) -> "Artifact":
        return cls(d["files"], d.get("meta"))

    def render(self, max_chars_per_file: int = 20000) -> str:
        """Human/LLM-readable dump of all files."""
        parts = []
        for name, text in self._files.items():
            body = text if len(text) <= max_chars_per_file else text[:max_chars_per_file] + "\n...[truncated]"
            parts.append(f"=== FILE: {name} ===\n{body}")
        return "\n\n".join(parts)


def parse_file_blocks(text: str) -> dict[str, str]:
    """Parse ``=== FILE: name ===`` sections (the format :meth:`Artifact.render` emits
    and our rewrite prompts ask for). Fenced code inside a section is unwrapped."""
    import re

    out: dict[str, str] = {}
    pieces = re.split(r"^=== FILE: (.+?) ===[ \t]*$", text or "", flags=re.M)
    for i in range(1, len(pieces) - 1, 2):
        name = pieces[i].strip()
        body = pieces[i + 1].strip("\n")
        m = re.match(r"^```[^\n]*\n(.*)\n```\s*$", body, re.S)
        if m:
            body = m.group(1)
        out[name] = body.rstrip() + "\n"
    return out
