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
import fnmatch
import hashlib
import json
import re
from pathlib import Path, PurePosixPath
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
        """Unified diff from ``self`` (old) to ``other`` (new).

        Every diff line ends in a newline; a file whose last line has no newline
        gets git's ``\\ No newline at end of file`` marker, so ``-old`` / ``+new``
        lines never run together (which would hide added lines from critics)."""
        chunks = []
        for name in self.changed_files(other):
            chunks.extend(_file_diff(name, self._files.get(name, ""), other._files.get(name, ""), context))
        return "".join(chunks)

    def diff_size(self, other: "Artifact") -> int:
        """Number of added+removed lines between two versions."""
        n = 0
        for name in self.changed_files(other):
            lines = list(_file_diff(name, self._files.get(name, ""), other._files.get(name, ""), 0))
            # skip the two file headers; count hunk body lines (content starting with '--'/'++' included)
            n += sum(1 for line in lines[2:] if line[:1] in "+-")
        return n

    # persistence
    @classmethod
    def from_dir(cls, path: str | Path, patterns: Iterable[str] = ("**/*",), exclude: Iterable[str] = ()) -> "Artifact":
        """Read text files under ``path`` (binary files are skipped). ``exclude``
        entries are matched (exactly or as glob patterns) against every path part
        and against the relative path; ``__pycache__``, ``.git`` and
        ``.pytest_cache`` are always excluded."""
        root = Path(path)
        files = {}
        excl = tuple(exclude) + ("__pycache__", ".git", ".pytest_cache")

        def excluded(rel: Path) -> bool:
            s = rel.as_posix()
            return any(part == pat or fnmatch.fnmatchcase(part, pat) for part in rel.parts for pat in excl) or \
                any(fnmatch.fnmatchcase(s, pat) for pat in excl)

        for pat in patterns:
            for p in root.glob(pat):
                if p.is_file() and not excluded(p.relative_to(root)):
                    try:
                        files[p.relative_to(root).as_posix()] = p.read_text()
                    except UnicodeDecodeError:
                        continue
        return cls(files)

    def to_dir(self, path: str | Path, clean: bool = False) -> Path:
        """Write every file under ``path``. Raises ValueError (before writing
        anything) if a file name is absolute or escapes ``path`` via ``..``."""
        root = Path(path)
        bad = [n for n in self._files if not is_safe_relpath(n)]
        if bad:
            raise ValueError(f"unsafe artifact file names (absolute or escaping the directory): {bad[:5]}")
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


def is_safe_relpath(name: str) -> bool:
    """True if ``name`` is a relative path that stays inside its root directory."""
    if not name or name.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:[\\/]", name):
        return False
    parts = PurePosixPath(name.replace("\\", "/")).parts
    return ".." not in parts and "\x00" not in name


def _file_diff(name: str, old: str, new: str, context: int):
    a = old.splitlines(keepends=True)
    b = new.splitlines(keepends=True)
    for line in difflib.unified_diff(a, b, f"a/{name}", f"b/{name}", n=context):
        yield line if line.endswith("\n") else line + "\n\\ No newline at end of file\n"


def parse_file_blocks(text: str) -> dict[str, str]:
    """Parse ``=== FILE: name ===`` sections (the format :meth:`Artifact.render` emits
    and our rewrite prompts ask for). Fenced code inside a section is unwrapped."""
    import re

    out: dict[str, str] = {}
    pieces = re.split(r"^=== FILE: (.+?) ===[ \t\r]*$", text or "", flags=re.M)
    for i in range(1, len(pieces) - 1, 2):
        name = pieces[i].strip()
        body = pieces[i + 1].strip("\n")
        m = re.match(r"^```[^\n]*\n(.*)\n```\s*$", body, re.S)
        if m:
            body = m.group(1)
        out[name] = body.rstrip() + "\n"
    return out
