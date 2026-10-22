"""A real git working tree for the Solidifier: a port of Evolver ``src/gep/gitOps.js``."""
from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path
from typing import Mapping, Optional

DIFF_SNAPSHOT_MAX_CHARS = 8000
CRITICAL_PROTECTED_PREFIXES = ("skills/skill-tools/", "skills/git-sync/", "skills/evolver/")
CRITICAL_PROTECTED_FILES = ("MEMORY.md", "SOUL.md", "IDENTITY.md", "AGENTS.md", "USER.md", "HEARTBEAT.md",
                            "RECENT_EVENTS.md", "TOOLS.md", "TROUBLESHOOTING.md", "openclaw.json", "evolver.json",
                            ".env", "package.json")


def is_critical_protected_path(rel: str) -> bool:
    rel = re.sub(r"^\./+", "", str(rel or "").replace("\\", "/")).strip()
    if not rel:
        return False
    for pre in CRITICAL_PROTECTED_PREFIXES:
        p = pre.rstrip("/")
        if rel == p or rel.startswith(p + "/"):
            return True
    return rel in CRITICAL_PROTECTED_FILES


class GitWorkspace:
    def __init__(self, root: str | Path, *, env: Optional[dict] = None) -> None:
        self.root = Path(root)
        self.env = {**os.environ, "GIT_AUTHOR_NAME": "evomap", "GIT_AUTHOR_EMAIL": "evomap@localhost",
                    "GIT_COMMITTER_NAME": "evomap", "GIT_COMMITTER_EMAIL": "evomap@localhost", **(env or {})}
        self.log: list[str] = []

    def _git(self, *args: str, check: bool = False) -> subprocess.CompletedProcess:
        r = subprocess.run(["git", *args], cwd=self.root, env=self.env, capture_output=True, text=True, timeout=60)
        if check and r.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} failed: {r.stderr.strip()}")
        return r

    def _lines(self, *args: str) -> list[str]:
        r = self._git(*args)
        return [ln.strip() for ln in r.stdout.splitlines() if ln.strip()] if r.returncode == 0 else []

    def is_git_repo(self) -> bool:
        return self.root.is_dir() and self._git("rev-parse", "--git-dir").returncode == 0

    @classmethod
    def init(cls, root: str | Path, files: Mapping[str, str], message: str = "baseline") -> "GitWorkspace":
        ws = cls(root)
        ws.root.mkdir(parents=True, exist_ok=True)
        ws._git("init", "-q", check=True)
        ws.write(files)
        ws._git("add", "-A", check=True)
        ws._git("commit", "-q", "-m", message, check=True)
        return ws

    def write(self, files: Mapping[str, str], *, delete_missing_from: Optional[Mapping[str, str]] = None) -> None:
        """Mirror a cycle's ``after`` map into the tree; delete paths of ``delete_missing_from`` it lacks."""
        for rel, text in files.items():
            p = self.root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
        for rel in (delete_missing_from or {}):
            if rel not in files and (self.root / rel).exists():
                (self.root / rel).unlink()

    def untracked_files(self) -> list[str]:
        return self._lines("ls-files", "--others", "--exclude-standard")

    def changed_files(self) -> list[str]:
        """``gitListChangedFiles``: unstaged, staged and untracked paths."""
        cmds = (("diff", "--name-only"), ("diff", "--cached", "--name-only"),
                ("ls-files", "--others", "--exclude-standard"))
        return list(dict.fromkeys(f for args in cmds for f in self._lines(*args)))

    def diff_snapshot(self) -> str:
        """``captureDiffSnapshot``: unstaged + staged diff, truncated at 8000 chars."""
        parts = [r.stdout for r in (self._git("diff"), self._git("diff", "--cached")) if r.returncode == 0 and r.stdout]
        combined = "\n".join(parts)
        if len(combined) > DIFF_SNAPSHOT_MAX_CHARS:
            combined = combined[:DIFF_SNAPSHOT_MAX_CHARS] + "\n... [TRUNCATED]"
        return combined

    def blast_radius(self, policy=None) -> dict:
        """Files and ``--numstat`` lines (plus new untracked files' lines) under the counted-file policy."""
        from .solidify import CountedFilePolicy
        policy = policy or CountedFilePolicy()
        changed = self.changed_files()
        counted = [f for f in changed if policy.counts(f)]
        lines = 0
        for args in (("diff", "--numstat"), ("diff", "--cached", "--numstat")):
            for ln in self._lines(*args):
                parts = ln.split("\t")
                if len(parts) == 3 and policy.counts(parts[2]) and parts[0].isdigit() and parts[1].isdigit():
                    lines += int(parts[0]) + int(parts[1])
        for f in self.untracked_files():
            if policy.counts(f):  # every line of a new file is an addition
                text = (self.root / f).read_text() if (self.root / f).is_file() else ""
                lines += len(text.splitlines())
        return {"files": len(counted), "lines": lines, "changed": sorted(changed), "counted": sorted(counted)}

    def rollback(self, mode: str = "stash") -> str:
        """``EVOLVER_ROLLBACK_MODE``: ``stash`` (falls back to a hard reset), ``hard`` or ``none``."""
        mode = (mode or "stash").lower()
        if mode == "none":
            self.log.append("rollback none")
            return "none"
        if mode == "stash":
            ref = f"evolver-rollback-{int(time.time() * 1000)}"
            r = self._git("stash", "push", "-m", ref, "--include-untracked")
            if r.returncode == 0 and "No local changes" not in r.stdout:
                self.log.append(f"stashed {ref}")
                return "stash"
            self.log.append("stash failed or no changes; hard reset")
        self._git("restore", "--staged", "--worktree", ".")
        self._git("reset", "--hard")
        return "hard"

    def remove_new_untracked(self, baseline_untracked=(), cycle_started_at: Optional[float] = None) -> dict:
        """``rollbackNewUntrackedFiles``: delete untracked files not in the baseline (never protected paths), then
        prune empty directories that contain a "/" (never top-level), deepest first."""
        baseline = {str(x) for x in baseline_untracked}
        deleted, skipped = [], []
        for rel in self.untracked_files():
            if rel in baseline:
                continue
            p = self.root / rel
            if cycle_started_at is not None:
                try:
                    if p.stat().st_mtime * 1000 < cycle_started_at * 1000 - 1:
                        continue
                except OSError:
                    continue
            if is_critical_protected_path(rel):
                skipped.append(rel)
                continue
            if p.is_file():
                p.unlink()
                deleted.append(rel)
        to_check: set = set()
        for rel in deleted:
            d = os.path.dirname(rel)
            while d and d != ".":
                parent = os.path.dirname(d)
                if parent == d or "/" not in d:
                    break
                to_check.add(d)
                d = parent
        removed = []
        for d in sorted(to_check, key=len, reverse=True):
            dp = self.root / d
            if not is_critical_protected_path(d + "/") and dp.is_dir() and not any(dp.iterdir()):
                dp.rmdir()
                removed.append(d)
        return {"deleted": deleted, "skipped": skipped, "removed_dirs": removed}
