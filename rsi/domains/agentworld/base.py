"""AgentWorld environment base: virtual workspace, mini shell, subtasks, validity filter."""
from __future__ import annotations

import hashlib
import json
import random
import re
import shlex
from dataclasses import dataclass, field
from typing import Any, Optional

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

    def __init__(self, task_id: str, seed: int, n_subtasks: int) -> None:
        self.task_id = task_id
        self.rng = random.Random(f"agentworld|{task_id}|{seed}")
        self.files: dict[str, str] = {"docs/GUIDE.md": project_guide(random.Random(f"guide|{task_id}|{seed}"))}
        self.subtasks: list[Subtask] = []
        self.n_bash = 0
        self.log: list[str] = []

    # ---- workspace
    def read_file(self, path: str) -> Optional[str]:
        return self.files.get(path.lstrip("./") if not path.startswith("/") else path)

    def _read_any(self, path: str, rt=None) -> Optional[str]:
        if path.startswith(("/.solpi/", "/tmp/")):
            return (rt.store.get(path) if rt is not None else None)
        return self.files.get(path[2:] if path.startswith("./") else path)

    def tool_read(self, path: str, rt=None) -> ToolResult:
        text = self._read_any(path, rt)
        if text is None:
            return ToolResult(f"ENOENT: no such file: {path}", is_error=True)
        return ToolResult(text)

    def tool_write(self, path: str, content: str, rt=None) -> ToolResult:
        self.files[path] = content
        self.log.append(f"write {path}")
        return ToolResult(f"Successfully wrote {len(content.encode())} bytes to {path}")

    def tool_edit(self, path: str, old: str, new: str, rt=None) -> ToolResult:
        text = self.files.get(path)
        if text is None:
            return ToolResult(f"ENOENT: no such file: {path}", is_error=True)
        n = text.count(old) if old else 0
        if n == 0:
            return ToolResult(f"Could not find the exact text in {path}. The old text must match exactly.",
                              is_error=True)
        if n > 1:
            return ToolResult(f"Found {n} occurrences of the text in {path}. The text must be unique.",
                              is_error=True)
        self.files[path] = text.replace(old, new, 1)
        self.log.append(f"edit {path}")
        return ToolResult(f"Successfully replaced text in {path}.")

    def tool_bash(self, command: str, rt=None) -> ToolResult:
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
            out = "\n".join(reversed(kept)) + f"\n\n[Showing last {len(kept)} of {len(lines)} lines. Full output: {path}]"
            details["fullOutputPath"] = path
        if code != 0:
            return ToolResult(out + f"\n\nCommand exited with code {code}", is_error=True, details=details)
        return ToolResult(out, details=details)

    # ---- shell
    def shell(self, command: str, rt=None) -> tuple[str, int]:
        cmd = command.strip()
        cmd = re.sub(r"\s*2>&1", "", cmd)
        parts = [p.strip() for p in cmd.split("|")]
        out, code = self.run_one(parts[0], rt)
        for filt in parts[1:]:
            out, fcode = self.filter(filt, out)
            if fcode:
                code = fcode if code == 0 else code
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
            names = sorted({f[len(d):].split("/")[0] for f in self.files if f.startswith(d)})
            return "\n".join(names), 0
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
            a, b = int(m.group(1)), int(m.group(2))
            return "\n".join(lines[a - 1:b]), 0
        if prog == "grep":
            count = "-c" in args
            number = "-n" in args
            fixed = "-F" in args
            limit = None
            pat = None
            i = 0
            while i < len(args):
                a = args[i]
                if a == "-m":
                    limit = int(args[i + 1])
                    i += 2
                    continue
                if a.startswith("-m") and a[2:].isdigit():
                    limit = int(a[2:])
                elif not a.startswith("-") and pat is None:
                    pat = a
                i += 1
            if pat is None:
                return "grep: missing pattern", 2
            rx = re.compile(re.escape(pat) if fixed else pat)
            hits = [(k + 1, ln) for k, ln in enumerate(lines) if rx.search(ln)]
            if limit is not None:
                hits = hits[:limit]
            if count:
                return str(len(hits)), 0 if hits else 1
            return "\n".join(f"{k}:{ln}" if number else ln for k, ln in hits), 0 if hits else 1
        return f"bash: {prog}: command not found", 127

    # ---- to implement
    def statement(self) -> str:
        raise NotImplementedError

    def family_command(self, argv: list[str], rt=None) -> tuple[str, int]:
        return f"bash: {argv[0]}: command not found", 127

    def verify(self) -> float:
        raise NotImplementedError

    def subtask_ok(self, st: Subtask) -> bool:
        raise NotImplementedError

    def apply_reference(self) -> None:
        """Apply the reference solution (hidden ground truth) - used only by the validity filter."""
        raise NotImplementedError


def validity_filter(env: Env) -> bool:
    """SoL-Pi's environment construction rule: keep an environment iff its verifier FAILS on the initial
    state and PASSES after the reference fix (task, trajectory and verifier are consistent)."""
    import copy
    if env.verify() >= 1.0:
        return False
    ref = copy.deepcopy(env)
    ref.apply_reference()
    return ref.verify() == 1.0


def project_guide(r: random.Random) -> str:
    """A long, generic contributor guide (~20-28 KB): the kind of large successful output that
    agents read early and then replay in every later request."""
    topics = ["build", "testing", "logging", "configuration", "deployment", "code style", "reviews", "releases",
              "observability", "security", "performance", "migrations"]
    out = ["# Contributor guide", ""]
    for t in r.sample(topics, len(topics)):
        out += [f"## {t.title()}", ""]
        for k in range(r.randint(14, 22)):
            out.append(f"- Rule {k + 1}: when changing {t}, keep modules small, document public functions, run the "
                       f"relevant checks locally and describe user-visible effects in the changelog ({r.randint(1, 99)}).")
        out.append("")
    return "\n".join(out)


def _h(*parts) -> int:
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:8], 16)


WORDS = ["alpha", "beta", "delta", "sigma", "omega", "kappa", "theta", "gamma", "lambda", "zeta", "rho", "tau"]
MODULES = ["pricing", "inventory", "shipping", "billing", "reports", "auth", "search", "cache", "ledger", "quota",
           "orders", "catalog", "profile", "notify", "sessions", "uploads", "metrics", "gateway", "tax", "refunds"]
CONSTS = ["SCALE", "OFFSET", "LIMIT", "RATE", "BATCH", "RETRIES", "WINDOW", "FACTOR", "MARGIN", "DEPTH"]
BASE_STATEMENT = "Work in the repository at /repo. "


