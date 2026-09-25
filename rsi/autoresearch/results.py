"""results.tsv and the kept-commit chain.

:class:`ResultsLog` writes upstream's table exactly: tab-separated, header
``commit <metric> memory_gb status description``, the metric with 6 decimals
(``0.000000`` for crashes), memory in GB with 1 decimal (``0.0`` for crashes),
status ``keep | discard | crash`` and a one-line description. Like upstream it
is never committed: discards live only as rows.

:class:`Workspace` is the branch ``autoresearch/<tag>``: a linear chain of kept
commits. ``commit`` puts an experiment on top of HEAD, ``reset_to`` drops it
again (the object stays in the store, as with ``git reset``), ``rewind`` moves
HEAD to an older kept commit (allowed, "very very sparingly"). The default
backend is in-memory (content-addressed commits); ``backend="git"`` drives a real
git repository in a directory of its own.
"""
from __future__ import annotations

import csv
import hashlib
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..core.artifact import Artifact
from ..core.ledger import ArtifactStore

STATUSES = ("keep", "discard", "crash")


@dataclass
class Row:
    commit: str
    metric: float
    memory_gb: float
    status: str
    description: str


class ResultsLog:
    """The 5-column ``results.tsv``."""

    def __init__(self, path: Optional[str | Path], metric_name: str = "val_bpb") -> None:
        self.path = Path(path) if path else None
        self.metric_name = metric_name
        self._rows: list[Row] = []

    @property
    def header(self) -> list[str]:
        return ["commit", self.metric_name, "memory_gb", "status", "description"]

    def init(self) -> None:
        """Create the file with just the header row (upstream setup step 5)."""
        self._rows = []
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("w", newline="") as f:
                csv.writer(f, delimiter="\t", lineterminator="\n").writerow(self.header)

    @staticmethod
    def _clean(desc: str) -> str:
        return " ".join(str(desc or "").replace("\t", " ").split())[:300]

    def append(self, commit: str, metric: Optional[float], memory_gb: float, status: str, description: str) -> Row:
        if status not in STATUSES:
            raise ValueError(f"status must be one of {STATUSES}, got {status!r}")
        if status == "crash":
            metric, memory_gb = 0.0, 0.0
        row = Row(str(commit)[:7], float(metric or 0.0), round(float(memory_gb or 0.0), 1), status,
                  self._clean(description))
        self._rows.append(row)
        if self.path:
            with self.path.open("a", newline="") as f:
                csv.writer(f, delimiter="\t", lineterminator="\n").writerow(
                    [row.commit, f"{row.metric:.6f}", f"{row.memory_gb:.1f}", row.status, row.description])
        return row

    def rows(self) -> list[Row]:
        return list(self._rows)

    @classmethod
    def read(cls, path: str | Path) -> "ResultsLog":
        p = Path(path)
        with p.open() as f:
            r = list(csv.reader(f, delimiter="\t"))
        log = cls(None, metric_name=r[0][1] if r else "score")
        for c, m, mem, st, d in r[1:]:
            log._rows.append(Row(c, float(m), float(mem), st, d))
        log.path = p
        return log

    def text(self, last: Optional[int] = None) -> str:
        rows = self._rows[-last:] if last else self._rows
        lines = ["\t".join(self.header)]
        lines += [f"{r.commit}\t{r.metric:.6f}\t{r.memory_gb:.1f}\t{r.status}\t{r.description}" for r in rows]
        return "\n".join(lines) + "\n"


@dataclass
class Commit:
    sha: str
    parent: Optional[str]
    artifact_id: str
    message: str
    t: float

    @property
    def short(self) -> str:
        return self.sha[:7]


class Workspace:
    """A single-branch, kept-commits-only chain (``autoresearch/<tag>``)."""

    def __init__(self, store: ArtifactStore, *, backend: str = "memory", root: Optional[str | Path] = None) -> None:
        if backend not in ("memory", "git"):
            raise ValueError("backend must be 'memory' or 'git'")
        if backend == "git" and root is None:
            raise ValueError("backend='git' needs root=")
        self.store = store
        self.backend = backend
        self.root = Path(root) if root else None
        self.branch: Optional[str] = None
        self.commits: dict[str, Commit] = {}
        self._head: Optional[str] = None
        self.n_resets = 0
        self.n_rewinds = 0
        self._artifacts: dict[str, Artifact] = {}

    # ---- git plumbing (optional backend)
    def _git(self, *args: str) -> str:
        r = subprocess.run(["git", *args], cwd=self.root, capture_output=True, text=True, check=True,
                           env={"GIT_AUTHOR_NAME": "autoresearch", "GIT_AUTHOR_EMAIL": "autoresearch@localhost",
                                "GIT_COMMITTER_NAME": "autoresearch", "GIT_COMMITTER_EMAIL": "autoresearch@localhost",
                                "PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(self.root)})
        return r.stdout.strip()

    def _git_write(self, artifact: Artifact) -> None:
        """Make the working tree exactly ``artifact`` (nested files included), keeping
        ``.git`` and the untracked ``results.tsv`` / ``run.log``."""
        import shutil

        for p in self.root.iterdir():
            if p.name in (".git", ".gitignore", "results.tsv", "run.log"):
                continue
            if p.is_dir() and not p.is_symlink():
                shutil.rmtree(p)
            else:
                p.unlink()
        artifact.to_dir(self.root)

    # ---- API
    def init_run(self, tag: str, base: Artifact, message: str = "baseline") -> str:
        """Create the fresh branch ``autoresearch/<tag>`` holding ``base``."""
        if self.branch is not None:
            raise RuntimeError(f"branch {self.branch} already exists: a run tag must be fresh")
        self.branch = f"autoresearch/{tag}"
        if self.backend == "git":
            self.root.mkdir(parents=True, exist_ok=True)
            if (self.root / ".git").exists():
                raise RuntimeError(f"{self.root} already holds a repository")
            self._git("init", "-q", "-b", self.branch)
            (self.root / ".gitignore").write_text("results.tsv\nrun.log\n")
        return self.commit(base, message)

    def commit(self, artifact: Artifact, message: str) -> str:
        self.store.put(artifact)
        self._artifacts[artifact.id] = artifact
        if self.backend == "git":
            self._git_write(artifact)
            self._git("add", "-A")
            self._git("commit", "-q", "--allow-empty", "-m", message or "experiment")
            sha = self._git("rev-parse", "HEAD")
        else:
            sha = hashlib.sha1(f"{self._head}|{artifact.id}|{message}|{len(self.commits)}".encode()).hexdigest()
        self.commits[sha] = Commit(sha, self._head, artifact.id, message, time.time())
        self._head = sha
        return sha

    def amend(self, artifact: Artifact, message: str) -> str:
        """Replace HEAD (used by fix-and-rerun of a trivially crashed experiment)."""
        parent = self.commits[self._head].parent
        self.reset_to(parent, count=False)
        return self.commit(artifact, message)

    def reset_to(self, sha: str, count: bool = True) -> None:
        if sha not in self.commits:
            raise KeyError(sha)
        if self.backend == "git":
            self._git("reset", "-q", "--hard", sha)
        self._head = sha
        if count:
            self.n_resets += 1

    def rewind(self, sha: str) -> None:
        """Move HEAD to an older kept commit (rare by design)."""
        self.reset_to(sha, count=False)
        self.n_rewinds += 1

    def head(self) -> str:
        return self._head

    def artifact(self, sha: Optional[str] = None) -> Artifact:
        aid = self.commits[sha or self._head].artifact_id
        return self._artifacts.get(aid) or self.store.get(aid)

    def log(self, n: int = 20) -> list[Commit]:
        """Kept chain from HEAD backwards (``git log --oneline``)."""
        out, cur = [], self._head
        while cur is not None and len(out) < n:
            c = self.commits[cur]
            out.append(c)
            cur = c.parent
        return out

    def log_text(self, n: int = 20) -> str:
        return "\n".join(f"{c.short} {c.message}" for c in self.log(n))
