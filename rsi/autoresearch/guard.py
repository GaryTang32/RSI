"""Mechanical versions of upstream's house rules.

Upstream autoresearch protects its grader and budget *by instruction only*
(``program.md``: "Modify prepare.py. It is read-only."; "If a run exceeds 10
minutes, kill it"), and the agent itself enforces them. The hardened mode of
this package enforces them in code:

* :class:`ScopeGuard` - edits outside ``editable_paths`` (or to locked files) are
  rejected before anything runs; locked files are restored from a sealed copy,
  made read-only and hash-checked before and after every run. Because the
  editable code runs in the same process as the locked evaluator, the guard also
  scans the *added* lines of editable files for references to grader internals
  (``tamper`` patterns, see :func:`default_tamper_patterns`): writing the
  framework's result record, monkeypatching or importing private names of a
  locked module, naming hidden splits. This is a denylist, like the core
  :class:`~rsi.core.LeakageCritic`'s first stage: it stops the catalogued
  exploit classes, not a determined adversary (see the impl notes).
* :class:`BudgetEnforcer` - the fixed budget lives in locked code (the task's
  locked dataloader/clock), the framework passes it in, kills the process at
  ``kill_after`` (the watchdog, via :func:`rsi.core.sandbox.run_cmd`) and treats
  over-long runs as failures.
* :class:`CrashPolicy` - "if the grep output is empty, the run crashed"; trivial
  crashes (typo, missing import) get at most ``max_fix_attempts`` fix-and-rerun
  cycles; hangs, OOM and NaN runs are logged as crashes and skipped.
"""
from __future__ import annotations

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
    """fnmatch against any pattern (``**`` matches everything)."""
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


#: Environment variables through which the framework talks to the locked code
#: (mode, seed, budget, data dir, result-record path). Editable code never needs them.
FRAMEWORK_ENV = r"RSI_AR_\w*"


#: Library modules the locked evaluators rely on. Rebinding their attributes in-process
#: (``np.take_along_axis = ...``, ``time.time = ...``) would change what the locked code computes.
PATCHABLE_LIBS = ("np", "numpy", "math", "time", "os", "sys", "json", "builtins", "sklearn", "scipy", "resource",
                  "random", "torch", "pd", "pandas")
_AUG = r"(?:[-+*/%&|^@]|//|\*\*|<<|>>)?=(?!=)"
_MUTATORS = ("fill", "put", "itemset", "setflags", "resize", "sort", "clear", "update", "pop", "popitem",
             "setdefault", "append", "extend", "insert", "remove", "__setitem__", "__delitem__")


def default_tamper_patterns(locked_paths: Sequence[str]) -> list[str]:
    """Regexes for added lines that reach into the grader.

    For every locked Python module ``m`` (``m.py`` in ``locked_paths``, no glob):
    private names (``m._x``, ``from m import _x``); rebinding, subscript-assigning or
    mutating its attributes (``m.f = ...``, ``m.X[:] = ...``, ``m.X.fill(...)``,
    ``setattr``/``delattr``, ``m.__dict__``, ``vars(m)``, ``sys.modules['m']``);
    aliasing it (``import m as M``, which would dodge every other pattern).
    Generic: the framework's environment variables (:data:`FRAMEWORK_ENV`, which carry
    the path of the framework-owned result record), dynamic imports and frame/gc
    introspection, and rebinding attributes of the library modules the locked
    evaluators use (:data:`PATCHABLE_LIBS`). The locked evaluators additionally check
    their own integrity at scoring time (see ``rsi.domains.tinylm.prepare._tampered``)."""
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
    """Lines of ``after`` that a unified diff from ``before`` marks as added."""
    import difflib

    return [l[1:] for l in difflib.unified_diff(before.splitlines(), after.splitlines(), lineterm="", n=0)
            if l.startswith("+") and not l.startswith("+++")]


class ScopeGuard:
    """Hardened replacement for the "What you CANNOT do" list.

    ``check(base, cand)`` lists edits outside the editable paths and added lines
    of editable files that match a ``tamper`` pattern; a non-empty list means the
    proposal is rejected without running. ``sealed`` holds the canonical locked
    files; :meth:`locked_hashes_ok` compares a working directory (or artifact)
    against them.
    """

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
        """Snippets of the added lines that match a tamper pattern."""
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
        """Names of locked files whose content differs from the sealed copy
        (``where`` is a directory or an Artifact). Empty list = all good."""
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
        """Make locked files read-only in a working directory (the "read-only mount")."""
        for name in self.sealed:
            p = Path(workdir) / name
            if p.exists():
                os.chmod(p, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)


class BudgetEnforcer:
    """Framework side of the fixed budget.

    The in-process half lives in the task's locked code (e.g. tinylm's
    ``prepare.make_dataloader`` stops yielding once the locked clock is spent and
    ``evaluate_bpb`` flags overruns). This half supplies the watchdog timeout and
    classifies finished runs:

    * killed by the watchdog -> crash ("treat it as a failure");
    * ``kind == "ceiling"`` and wall time above ``amount`` -> over budget
      (the xgboost port logs these as ``discard`` with their score).
    """

    def __init__(self, budget) -> None:
        self.budget = budget
        self.n_killed = 0
        self.n_over = 0

    @property
    def kill_after(self) -> float:
        return self.budget.hard_timeout()

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


class CrashPolicy:
    """Upstream crash handling, made explicit.

    ``kind(outcome)`` -> ``"ok" | "timeout" | "oom" | "nan" | "violation" |
    "trivial" | "error"``. Only ``trivial`` crashes (typos, missing imports - the
    "dumb and easy to fix" cases) are sent back to the agent for a fix, at most
    ``max_fix_attempts`` times ("more than a few attempts" -> give up); every
    other crash is logged with status ``crash`` (0.000000 / 0.0) and skipped.
    """

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
            k = "timeout"
        elif "MemoryError" in log or "out of memory" in reason.lower():
            k = "oom"
        elif re.search(r"^FAIL\s*$", log, re.M) or "FAIL" in reason:
            k = "nan"
        elif reason.startswith(("locked", "non-causal", "budget overrun", "frame-dependent", "no locked result",
                                "PermissionError")):
            k = "violation"
        elif _TRIVIAL.search(log):
            k = "trivial"
        else:
            k = "error"
        return k

    def record(self, kind: str) -> None:
        self.counts[kind] = self.counts.get(kind, 0) + 1

    def should_fix(self, kind: str, attempts: int) -> bool:
        return kind in self.fix_kinds and attempts < self.max_fix_attempts
