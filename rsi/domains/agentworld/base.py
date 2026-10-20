"""AgentWorld environment base: virtual workspace, Pi-like file tools, a mini shell and the validity filter."""
from __future__ import annotations

import copy
import hashlib
import posixpath
import random
import re
import shlex
from dataclasses import dataclass, field
from typing import Any, Optional

from ...solpi.fusion import resolve_tool_path
from ...solpi.runtime import ToolResult

PI_MAX_BYTES = 50 * 1024
PI_MAX_LINES = 2000


@dataclass
class Subtask:
    id: str
    goal: str
    key: str                 # what identifies it in outputs (module / file / item / line number)
    data: dict = field(default_factory=dict)   # hidden ground truth for the verifier


class Env:
    family = "env"
    style = "per_subtask"            # per_subtask | batch
    cwd = "/repo"                    # workspace files are keyed by cwd-relative paths
    home = "/home/agent"
    resolves_tool_paths = True

    def __init__(self, task_id: str, seed: int, n_subtasks: int) -> None:
        self.task_id = task_id
        self.rng = random.Random(f"agentworld|{task_id}|{seed}")
        self.files: dict[str, str] = {"docs/GUIDE.md": project_guide(random.Random(f"guide|{task_id}|{seed}"))}
        self.subtasks: list[Subtask] = []
        self.n_bash = 0
        self.log: list[str] = []

    def resolve_path(self, path: str) -> str:
        """Workspace key of a tool path, resolved like Pi's ``resolveToCwd`` (cwd-relative inside ``cwd``)."""
        absolute = resolve_tool_path(path, self.cwd, self.home)
        root = self.cwd.rstrip("/") + "/"
        return absolute[len(root):] if absolute.startswith(root) else absolute

    @staticmethod
    def realpath(path: str) -> str:
        return posixpath.normpath(path)

    def read_file(self, path: str) -> Optional[str]:
        return self.files.get(self.resolve_path(path))

    def _read_any(self, path: str, rt=None) -> Optional[str]:
        if path.startswith(("/.solpi/", "/tmp/")):
            return rt.store.get(path) if rt is not None else None
        return self.files.get(self.resolve_path(path))

    def tool_read(self, path: str, rt=None) -> ToolResult:
        text = self._read_any(path, rt)
        return ToolResult(f"ENOENT: no such file: {path}", is_error=True) if text is None else ToolResult(text)

    def tool_write(self, path: str, content: str, rt=None) -> ToolResult:
        key = self.resolve_path(path)
        self.files[key] = content
        self.log.append(f"write {key}")
        return ToolResult(f"Successfully wrote {len(content.encode())} bytes to {path}")

    def tool_edit(self, path: str, old: str, new: str, rt=None) -> ToolResult:
        key = self.resolve_path(path)
        text = self.files.get(key)
        if text is None:
            return ToolResult(f"ENOENT: no such file: {path}", is_error=True)
        n = text.count(old) if old else 0
        if n == 0:
            return ToolResult(f"Could not find the exact text in {path}. The old text must match exactly.",
                              is_error=True)
        if n > 1:
            return ToolResult(f"Found {n} occurrences of the text in {path}. The text must be unique.",
                              is_error=True)
        self.files[key] = text.replace(old, new, 1)
        self.log.append(f"edit {key}")
        return ToolResult(f"Successfully replaced text in {path}.")

    def tool_bash(self, command: str, rt=None) -> ToolResult:
        """Pi's bash tool: an output over 50 KB / 2,000 lines keeps its tail; the full text goes to the store."""
        self.n_bash += 1
        try:
            out, code = self.shell(command, rt)
        except Exception as e:  # noqa: BLE001
            out, code = f"bash: {e}", 2
        details: dict[str, Any] = {"exit_code": code}
        lines = out.split("\n")
        if len(out.encode()) > PI_MAX_BYTES or len(lines) > PI_MAX_LINES:
            path = f"/tmp/pi-bash-{self.task_id.replace('/', '_')}-{self.n_bash}.log"
            if rt is not None:
                rt.store[path] = out
            kept, used = [], 0
            for ln in reversed(lines):
                if used + len(ln) + 1 > PI_MAX_BYTES or len(kept) >= PI_MAX_LINES:
                    break
                kept.append(ln)
                used += len(ln) + 1
            note = f"\n\n[Showing last {len(kept)} of {len(lines)} lines. Full output: {path}]"
            out = "\n".join(reversed(kept)) + note
            details["fullOutputPath"] = path
        if code != 0:
            return ToolResult(out + f"\n\nCommand exited with code {code}", is_error=True, details=details)
        return ToolResult(out, details=details)

    def shell(self, command: str, rt=None) -> tuple[str, int]:
        parts = [p.strip() for p in re.sub(r"\s*2>&1", "", command.strip()).split("|")]
        out, code = self.run_one(parts[0], rt)
        for filt in parts[1:]:
            out, fcode = self.filter(filt, out)
            if fcode and code == 0:
                code = fcode
        return out, code

    def run_one(self, cmd: str, rt=None) -> tuple[str, int]:
        try:
            argv = shlex.split(cmd)
        except ValueError as e:
            return f"bash: syntax error: {e}", 2
        if not argv:
            return "", 0
        prog = argv[0]
        if prog == "cat":
            chunks = []
            for p in argv[1:]:
                t = self._read_any(p, rt)
                if t is None:
                    return f"cat: {p}: No such file or directory", 1
                chunks.append(t)
            return "".join(chunks), 0
        if prog == "ls":
            d = argv[1].rstrip("/") + "/" if len(argv) > 1 else ""
            return "\n".join(sorted({f[len(d):].split("/")[0] for f in self.files if f.startswith(d)})), 0
        if prog in ("grep", "head", "tail", "wc", "sed") and len(argv) >= 2 and not argv[-1].startswith("-"):
            src = self._read_any(argv[-1], rt) if prog != "sed" or "/" in argv[-1] or "." in argv[-1] else None
            if src is not None:
                return self.filter(" ".join(shlex.quote(a) for a in argv[:-1]), src)
        return self.family_command(argv, rt)

    def filter(self, filt: str, text: str) -> tuple[str, int]:
        argv = shlex.split(filt)
        prog, args = argv[0], argv[1:]
        lines = text.split("\n")
        if prog in ("head", "tail"):
            n = 10
            if "-n" in args:
                n = int(args[args.index("-n") + 1])
            elif args and args[0].startswith("-") and args[0][1:].isdigit():
                n = int(args[0][1:])
            return "\n".join(lines[:n] if prog == "head" else lines[-n:]), 0
        if prog == "wc":
            return str(text.count("\n") + (0 if text.endswith("\n") else 1)), 0
        if prog == "sed":
            m = re.search(r"(\d+),(\d+)p", filt)
            if not m:
                return "sed: unsupported expression", 1
            return "\n".join(lines[int(m.group(1)) - 1:int(m.group(2))]), 0
        if prog == "grep":
            limit = pat = None
            i = 0
            while i < len(args):
                a = args[i]
                if a == "-m":
                    limit = int(args[i + 1])
                    i += 1
                elif a.startswith("-m") and a[2:].isdigit():
                    limit = int(a[2:])
                elif not a.startswith("-") and pat is None:
                    pat = a
                i += 1
            if pat is None:
                return "grep: missing pattern", 2
            rx = re.compile(re.escape(pat) if "-F" in args else pat)
            hits = [(k + 1, ln) for k, ln in enumerate(lines) if rx.search(ln)][:limit]
            code = 0 if hits else 1
            if "-c" in args:
                return str(len(hits)), code
            return "\n".join(f"{k}:{ln}" if "-n" in args else ln for k, ln in hits), code
        return f"bash: {prog}: command not found", 127

    def _numbered_goals(self) -> str:
        return "\n".join(f"{i + 1}. {s.goal}" for i, s in enumerate(self.subtasks))

    def statement(self) -> str:
        raise NotImplementedError

    def family_command(self, argv: list[str], rt=None) -> tuple[str, int]:
        return f"bash: {argv[0]}: command not found", 127

    def verify(self) -> float:
        return sum(self.subtask_ok(s) for s in self.subtasks) / len(self.subtasks)

    def subtask_ok(self, st: Subtask) -> bool:
        raise NotImplementedError

    def apply_reference(self) -> None:
        """Apply the hidden reference solution (used only by the validity filter)."""
        raise NotImplementedError


def validity_filter(env: Env) -> bool:
    """Keep an environment iff its verifier fails initially and passes after the reference fix."""
    if env.verify() >= 1.0:
        return False
    ref = copy.deepcopy(env)
    ref.apply_reference()
    return ref.verify() == 1.0


def project_guide(r: random.Random) -> str:
    """A long generic contributor guide (~20-28 KB) that agents read early and then replay."""
    topics = ["build", "testing", "logging", "configuration", "deployment", "code style", "reviews", "releases",
              "observability", "security", "performance", "migrations"]
    out = ["# Contributor guide", ""]
    for t in r.sample(topics, len(topics)):
        out += [f"## {t.title()}", ""]
        for k in range(r.randint(14, 22)):
            out.append(f"- Rule {k + 1}: when changing {t}, keep modules small, document public functions, run the "
                       f"relevant checks locally and describe user-visible effects in the changelog "
                       f"({r.randint(1, 99)}).")
        out.append("")
    return "\n".join(out)


def _h(*parts) -> int:
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:8], 16)


def _pytest_argv(argv: list[str]) -> list[str]:
    """``python -m pytest ...`` -> ``pytest ...``."""
    return ["pytest"] + argv[3:] if argv[0] == "python" and argv[1:3] == ["-m", "pytest"] else argv


WORDS = ["alpha", "beta", "delta", "sigma", "omega", "kappa", "theta", "gamma", "lambda", "zeta", "rho", "tau"]
MODULES = ["pricing", "inventory", "shipping", "billing", "reports", "auth", "search", "cache", "ledger", "quota",
           "orders", "catalog", "profile", "notify", "sessions", "uploads", "metrics", "gateway", "tax", "refunds"]
CONSTS = ["SCALE", "OFFSET", "LIMIT", "RATE", "BATCH", "RETRIES", "WINDOW", "FACTOR", "MARGIN", "DEPTH"]
BASE_STATEMENT = "Work in the repository at /repo. "
