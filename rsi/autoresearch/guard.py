"""Mechanical house rules: scope guard with tamper scan, budget enforcer and crash policy."""
from __future__ import annotations

import difflib
import fnmatch
import hashlib
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, Sequence

from ..core.artifact import Artifact


def matches(name: str, patterns: Sequence[str]) -> bool:
    return any(p in ("**", "*", "**/*") or fnmatch.fnmatch(name, p) for p in patterns)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@dataclass
class Violation:
    path: str
    kind: str          # locked_edit | out_of_scope | tamper
    detail: str = ""

    def __str__(self) -> str:
        return f"{self.kind}:{self.path}" + (f" ({self.detail})" if self.detail else "")


#: Environment variables through which the framework talks to the locked code.
FRAMEWORK_ENV = r"RSI_AR_\w*"
#: Library modules the locked evaluators rely on; rebinding their attributes would change what they compute.
PATCHABLE_LIBS = ("np", "numpy", "math", "time", "os", "sys", "json", "builtins", "sklearn", "scipy", "resource",
                  "random", "torch", "pd", "pandas")
_AUG = r"(?:[-+*/%&|^@]|//|\*\*|<<|>>)?=(?!=)"
_MUTATORS = ("fill", "put", "itemset", "setflags", "resize", "sort", "clear", "update", "pop", "popitem",
             "setdefault", "append", "extend", "insert", "remove", "__setitem__", "__delitem__")


def default_tamper_patterns(locked_paths: Sequence[str]) -> list[str]:
    """Regexes for added lines that reach into the grader (env vars, introspection, patching, locked modules)."""
    libs = "|".join(PATCHABLE_LIBS)
    pats = [FRAMEWORK_ENV, r"\b__import__\s*\(", r"\bimportlib\b", r"\bgc\s*\.\s*get_(?:objects|referrers|referents)\b",
            r"\bsys\s*\.\s*_getframe\b", r"\binspect\s*\.\s*(?:currentframe|stack|getmodule)\b",
            rf"^\s*(?:{libs})(?:\s*\.\s*\w+)+\s*{_AUG}", rf"\b(?:setattr|delattr)\s*\(\s*(?:{libs})\b"]
    for p in locked_paths:
        if not p.endswith(".py") or any(c in p for c in "*?[") or "/" in p:
            continue
        m = re.escape(p[:-3])
        pats += [rf"\b{m}\s*\.\s*_\w+", rf"\bfrom\s+{m}\s+import\s+.*\b_\w+",
                 rf"\b{m}\s*\.\s*\w+\s*{_AUG}", rf"\b{m}\s*\.\s*\w+\s*\[[^\]\n]*\]\s*{_AUG}",
                 rf"\b{m}\s*\.\s*\w+\s*\.\s*(?:{'|'.join(_MUTATORS)})\s*\(",
                 rf"\b(?:setattr|delattr)\s*\(\s*{m}\b", rf"\bdel\s+{m}\s*\.", rf"\b{m}\s*\.\s*__dict__",
                 rf"\bvars\s*\(\s*{m}\s*\)", rf"\bimport\s+{m}\s+as\b", rf"sys\.modules\s*\[\s*['\"]{m}['\"]"]
    return pats


def added_lines(before: str, after: str) -> list[str]:
    return [l[1:] for l in difflib.unified_diff(before.splitlines(), after.splitlines(), lineterm="", n=0)
            if l.startswith("+") and not l.startswith("+++")]


class ScopeGuard:
    """Rejects edits outside ``editable_paths``, edits to locked files and added lines matching a tamper pattern."""

    def __init__(self, editable_paths: Sequence[str], locked_paths: Sequence[str] = (),
                 sealed: Optional[Mapping[str, str]] = None, tamper: Sequence[str] = ()) -> None:
        self.editable = tuple(editable_paths)
        self.locked = tuple(locked_paths)
        self.sealed = dict(sealed or {})
        self.hashes = {k: _sha(v) for k, v in self.sealed.items()}
        self.tamper_patterns = tuple(tamper)
        self._tamper = [re.compile(p) for p in self.tamper_patterns]
        self.n_checked = 0
        self.n_rejected = 0

    def tamper_hits(self, before: str, after: str) -> list[str]:
        hits: list[str] = []
        for line in added_lines(before, after):
            code = line.split("#", 1)[0]
            for rx in self._tamper:
                m = rx.search(code)
                if m and m.group(0) not in hits:
                    hits.append(m.group(0))
        return hits

    def check(self, base: Artifact, cand: Artifact) -> list[Violation]:
        self.n_checked += 1
        out = []
        for name in base.changed_files(cand):
            if matches(name, self.locked) or name in self.sealed:
                out.append(Violation(name, "locked_edit", "locked file (the grader/data/budget) was edited"))
            elif not matches(name, self.editable):
                out.append(Violation(name, "out_of_scope", f"only {list(self.editable)} may be edited"))
            elif self._tamper and cand.get(name) is not None:
                hits = self.tamper_hits(base.get(name) or "", cand[name])
                if hits:
                    out.append(Violation(name, "tamper", "reaches into the grader or hidden data: "
                                         + ", ".join(repr(h) for h in hits[:4])))
        if out:
            self.n_rejected += 1
        return out

    def locked_hashes_ok(self, where) -> list[str]:
        """Locked files (in a directory or Artifact) that differ from the sealed copy; empty means all good."""
        bad = []
        for name, h in self.hashes.items():
            if isinstance(where, Artifact):
                text = where.get(name)
            else:
                p = Path(where) / name
                text = p.read_text() if p.exists() else None
            if text is None or _sha(text) != h:
                bad.append(name)
        return bad

    def restore(self, artifact: Artifact) -> Artifact:
        return artifact.with_files(self.sealed) if self.sealed else artifact

    def lock_dir(self, workdir) -> None:
        for name in self.sealed:
            p = Path(workdir) / name
            if p.exists():
                os.chmod(p, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)


class BudgetEnforcer:
    """Counts runs killed by the watchdog and runs over a ``ceiling`` budget."""

    def __init__(self, budget) -> None:
        self.budget = budget
        self.n_killed = 0
        self.n_over = 0

    def classify(self, outcome) -> Optional[str]:
        if outcome.killed:
            self.n_killed += 1
            return "timeout"
        if outcome.over_budget:
            self.n_over += 1
            return "over_budget"
        return None


_TRIVIAL = re.compile(r"^(SyntaxError|IndentationError|TabError|NameError|ImportError|ModuleNotFoundError|"
                      r"UnboundLocalError)\b", re.M)
#: Crash kinds the agent may fix under ``Config(crash_fix="agent")``; timeouts and violations are never fixed.
AGENT_FIX_KINDS = ("trivial", "oom", "nan", "error")


class CrashPolicy:
    """Classifies crashes; kinds in ``fix_kinds`` go back to the agent at most ``max_fix_attempts`` times."""

    def __init__(self, max_fix_attempts: int = 3, fix_kinds: Sequence[str] = ("trivial",)) -> None:
        self.max_fix_attempts = max_fix_attempts
        self.fix_kinds = tuple(fix_kinds)
        self.counts: dict[str, int] = {}

    def kind(self, outcome) -> str:
        if not outcome.crashed:
            return "ok"
        reason = outcome.crash_reason or ""
        log = outcome.log or ""
        if outcome.killed or reason.startswith("killed"):
            return "timeout"
        if "MemoryError" in log or "out of memory" in reason.lower():
            return "oom"
        if re.search(r"^FAIL\s*$", log, re.M) or "FAIL" in reason:
            return "nan"
        if reason.startswith(("locked", "non-causal", "budget overrun", "frame-dependent", "no locked result",
                              "PermissionError")):
            return "violation"
        return "trivial" if _TRIVIAL.search(log) else "error"

    def record(self, kind: str) -> None:
        self.counts[kind] = self.counts.get(kind, 0) + 1

    def should_fix(self, kind: str, attempts: int) -> bool:
        return kind in self.fix_kinds and attempts < self.max_fix_attempts
