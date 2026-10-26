"""Harnessing-level recursion: a per-domain library of adopted harnesses with an EXPERIENCE.md each."""
from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Iterable, Optional

from ..core.artifact import Artifact
from .guard import screen_tree
from .tree import census

INDEX_HEAD = ("Each entry is an earlier task in THIS domain whose harness was chosen and whose improvement record "
              "supported a transferable lesson. The harness is kept whole, not summarised. Browse this index, then "
              "open the entries that look relevant to the matter in front of you.\n")


def safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(name)) or "_"


def experience_md(task_id: str, label: str, tree: Artifact, why: str, edits: str, exp: dict) -> str:
    out = [f"# {task_id}", "", f"- adopted: `{label}`  (harness `{tree.short_id}`)", f"- components: {census(tree)}"]
    if why:
        out += ["", "## Why this was chosen", why.strip()]
    if edits:
        out += ["", "## What each edit did", edits.strip()]
    out += ["", "## Transferable lesson"] + [f"- **{k}**: {exp[k]}" for k in ("cue", "lesson", "evidence",
                                                                              "confidence") if exp.get(k)]
    return "\n".join(out) + "\n"


class Library:
    """``<root>/<domain>/INDEX.md`` and ``harnesses/<task>/{harness/, EXPERIENCE.md}``."""

    def __init__(self, root: str | Path, markers: Iterable[str] = ()) -> None:
        self.root = Path(root)
        self.markers = tuple(markers)

    def dir(self, domain_key: str) -> Path:
        return self.root / safe(domain_key)

    def entries(self, domain_key: str) -> list[str]:
        h = self.dir(domain_key) / "harnesses"
        return sorted(p.parent.name for p in h.glob("*/EXPERIENCE.md")) if h.exists() else []

    def size(self) -> int:
        return sum(1 for _ in self.root.glob("*/harnesses/*/EXPERIENCE.md")) if self.root.exists() else 0

    def clear(self) -> None:
        if self.root.exists():
            shutil.rmtree(self.root)

    def context(self, domain_key: str) -> dict[str, str]:
        """Every file of the domain's library (INDEX.md first); empty while it has no EXPERIENCE.md."""
        if not self.entries(domain_key):
            return {}
        d = self.dir(domain_key)
        files = {"INDEX.md": (d / "INDEX.md").read_text()}
        for p in sorted(d.glob("harnesses/**/*")):
            if p.is_file():
                files[p.relative_to(d).as_posix()] = p.read_text()
        screen_tree(files, self.markers, f"library/{domain_key}")
        return files

    def add(self, domain_key: str, task_id: str, label: str, tree: Artifact, why: str,
            reflection: dict) -> Optional[str]:
        """Store the adopted tree + EXPERIENCE.md only for a valid experience; returns the entry path or None."""
        exp = reflection.get("experience") or {}
        if not all(exp.get(k) for k in ("cue", "lesson", "evidence")):
            return None
        md = experience_md(task_id, label, tree, why, reflection.get("edits", ""), exp)
        screen_tree({**tree.files, "EXPERIENCE.md": md}, self.markers, f"library entry {task_id}")
        entry = self.dir(domain_key) / "harnesses" / safe(task_id)
        tree.to_dir(entry / "harness", clean=True)
        (entry / "EXPERIENCE.md").write_text(md)
        self.rebuild_index(domain_key)
        return str(entry)

    def rebuild_index(self, domain_key: str) -> None:
        d = self.dir(domain_key)
        lines = [f"# Library: {domain_key}", "", INDEX_HEAD]
        for task in self.entries(domain_key):
            tree = Artifact.from_dir(d / "harnesses" / task / "harness")
            md = (d / "harnesses" / task / "EXPERIENCE.md").read_text()
            cue = next((ln.split(":", 1)[1].strip() for ln in md.splitlines() if ln.startswith("- **cue**")), "")
            lines += [f"- `harnesses/{task}/harness/` -- {census(tree)}",
                      f"  `harnesses/{task}/EXPERIENCE.md` -- cue: {cue}"]
        (d / "INDEX.md").write_text("\n".join(lines) + "\n")
