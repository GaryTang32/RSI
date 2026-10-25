"""Harness trees: the domain's Artifact, its editable surface, and structural validation before any rollout."""
from __future__ import annotations

import fnmatch
import re
from typing import Optional, Sequence

from ..core.artifact import Artifact, is_safe_relpath

PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
TEMPLATE_TAGS = ("{{", "{%", "{#")


def is_code(path: str) -> bool:
    return path.endswith(".py")


def editable_globs(seed: Artifact, editable: Optional[Sequence[str]]) -> list[str]:
    return list(editable) if editable is not None else sorted(seed)


def off_surface(names: Sequence[str], editable: Sequence[str]) -> list[str]:
    """Names a session may not write: unsafe paths or paths matching no editable glob."""
    return [n for n in names if not is_safe_relpath(n) or not any(fnmatch.fnmatch(n, p) for p in editable)]


def placeholders(text: str) -> set[str]:
    return set(PLACEHOLDER.findall(text or ""))


def validate(tree: Artifact, base: Artifact, *, domain=None, max_text_chars: int = 24000) -> list[str]:
    """Every problem with ``tree`` (empty list = valid); ``base`` is the stage's starting tree."""
    errs: list[str] = []
    if not len(tree):
        return ["the harness is empty"]
    for path, text in tree.items():
        if is_code(path):
            try:
                compile(text, path, "exec")
            except SyntaxError as e:
                errs.append(f"{path} does not compile: {e.msg} (line {e.lineno})")
            continue
        if tags := [t for t in TEMPLATE_TAGS if t in text]:
            errs.append(f"{path} contains template braces {tags}; use a single brace, or words")
    known = set().union(*(placeholders(t) for p, t in base.items() if not is_code(p)))
    for path, text in base.items():
        if is_code(path) or not (need := placeholders(text)):
            continue
        if missing := sorted(need - placeholders(tree.get(path, ""))):
            errs.append(f"{path} lost placeholders {['{' + m + '}' for m in missing]}; keep every placeholder")
    for path, text in tree.items():
        if not is_code(path) and (new := sorted(placeholders(text) - known)):
            errs.append(f"{path} adds placeholders {['{' + m + '}' for m in new]} that no code fills")
    code = "\n".join(t for p, t in tree.items() if is_code(p))
    if unloaded := [p for p in sorted(set(tree) - set(base)) if p not in code]:
        errs.append(f"new files no code file loads: {unloaded}; name each path in the code in the same edit")
    if (n := sum(len(t) for p, t in tree.items() if not is_code(p))) > max_text_chars:
        errs.append(f"harness text is {n} chars, over the cap of {max_text_chars}; cut text to pay for new text")
    hook = getattr(domain, "validate_artifact", None)
    if callable(hook):
        try:
            msg = hook(tree)
        except ValueError as e:
            msg = str(e)
        if msg:
            errs.append(str(msg))
    return errs


def census(tree: Artifact) -> str:
    """One-line inventory for library indexes."""
    code = [p for p in tree if is_code(p)]
    text = [p for p in tree if not is_code(p)]
    chars = sum(len(tree[p]) for p in text)
    return f"{len(tree)} files ({len(code)} code, {len(text)} text); {chars} text chars"


def diff_summary(old: Artifact, new: Artifact) -> dict:
    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    modified = sorted(p for p in set(old) & set(new) if old[p] != new[p])
    return {"added": added, "removed": removed, "modified": modified}
