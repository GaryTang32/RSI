"""Action Fusion: an edit/write and its follow-up command in ONE tool call.

Port of ``src/sol-pi/extensions/action-fusion/{index,then-run,file-queue}.ts``
(spec B3.4). The built-in ``edit`` and ``write`` tools are replaced by versions
with an optional ``then_run = {command, timeout?}`` parameter:

1. the path is canonicalised (``@``, unicode spaces, ``file://``, ``~``);
2. inside a per-file FIFO queue the built-in mutation runs; if it fails and
   ``then_run`` was given, the error gains ``[then_run:skipped] The file mutation did
   not complete successfully; the command was not run.``;
3. the file is hashed, the queue yields (other writers may run), and hashed again;
   if the content changed: ``[then_run:skipped] target content changed after the
   fused mutation; the command was not run.``;
4. the command runs through the bash tool: success appends
   ``[then_run:succeeded]\\n<output>``; a non-zero exit raises
   ``<mutation output>\\n\\n[then_run:failed]\\n\\n<error>`` - the edit is kept.

This removes the decision-free model turn between an edit and its predictable
check ("1 model round-trip avoided").
"""
from __future__ import annotations

import hashlib
import re
import threading
from typing import Callable, Optional

from .runtime import AgentRuntime, Extension, ToolError, ToolResult, ToolSpec

THEN_RUN_SUCCEEDED = "[then_run:succeeded]"
THEN_RUN_FAILED = "[then_run:failed]"
THEN_RUN_SKIPPED = "[then_run:skipped]"
SKIPPED_FAILED_MUTATION = f"{THEN_RUN_SKIPPED} The file mutation did not complete successfully; the command was not run."
SKIPPED_CHANGED = f"{THEN_RUN_SKIPPED} target content changed after the fused mutation; the command was not run."
EDIT_THEN_RUN_DESCRIPTION = ("Command to run next on this file after the edit succeeds — e.g. run, build, "
                             "start/restart, install, or check it; optional timeout in seconds. Skipped if the edit "
                             "fails; a non-zero exit is reported but keeps the edit.")


def resolve_tool_path(path: str) -> str:
    """Canonicalise a tool path the way ``resolveToolPath`` does (subset relevant here)."""
    p = str(path or "")
    p = re.sub(r"[  -​  　]", " ", p).strip()
    if p.startswith("@"):
        p = p[1:]
    if p.startswith("file://"):
        p = p[len("file://"):]
    if p.startswith("~/"):
        p = p[2:]
    while p.startswith("./"):
        p = p[2:]
    return p


class ActionFusion(Extension):
    """``then_run`` on edit/write (see module docstring). ``yield_hook(rt, path)`` runs
    between the two hashes - tests use it to simulate an interleaved writer."""

    name = "action_fusion"

    def __init__(self, yield_hook: Optional[Callable[[AgentRuntime, str], None]] = None) -> None:
        super().__init__()
        self.yield_hook = yield_hook
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    def _queue(self, path: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(path, threading.Lock())

    def register(self, rt: AgentRuntime) -> None:
        for tname in ("edit", "write"):
            if tname not in rt.tools:
                continue
            base = rt.tools[tname]
            spec = ToolSpec(tname, base.description + " Optional then_run: " + EDIT_THEN_RUN_DESCRIPTION,
                            {**base.parameters, "then_run": "{command: string, timeout?: seconds}"},
                            self._make(tname))
            rt.register_tool(spec, replaces=tname)

    @staticmethod
    def _file_hash(rt: AgentRuntime, path: str) -> str:
        text = rt.env.read_file(path) if rt.env is not None else None
        return hashlib.sha256((text if text is not None else "\0missing").encode()).hexdigest()

    def _make(self, tname: str):
        def execute(args: dict, rt: AgentRuntime, cid: str) -> ToolResult:
            then_run = args.pop("then_run", None)
            path = resolve_tool_path(args.get("path", ""))
            args["path"] = path
            with self._queue(path):
                base = rt.builtin(tname)
                try:
                    result = base.execute(args, rt, cid)
                except ToolError as e:
                    if then_run:
                        self.stats["skipped_failed_mutation"] += 1
                        raise ToolError(f"{e}\n\n{SKIPPED_FAILED_MUTATION}") from e
                    raise
                if result.is_error:
                    if then_run:
                        self.stats["skipped_failed_mutation"] += 1
                        raise ToolError(f"{result.content}\n\n{SKIPPED_FAILED_MUTATION}")
                    return result
                if not then_run:
                    return result
                command = then_run.get("command") if isinstance(then_run, dict) else str(then_run)
                if not command:
                    return result
                h1 = self._file_hash(rt, path)
                if self.yield_hook is not None:
                    self.yield_hook(rt, path)
                h2 = self._file_hash(rt, path)
                if h1 != h2:
                    self.stats["skipped_changed"] += 1
                    raise ToolError(SKIPPED_CHANGED)
                self.stats["fused"] += 1
                bash = rt.builtin("bash")
                out = bash.execute({"command": command, "timeout": (then_run or {}).get("timeout")
                                    if isinstance(then_run, dict) else None}, rt, cid)
                details = {**result.details, "then_run": {"command": command, "exit_error": out.is_error}}
                if out.is_error:
                    self.stats["then_run_failed"] += 1
                    return ToolResult(f"{result.content}\n\n{THEN_RUN_FAILED}\n\n{out.content}", is_error=True,
                                      details=details)
                self.stats["then_run_succeeded"] += 1
                return ToolResult(f"{result.content}\n{THEN_RUN_SUCCEEDED}\n{out.content}", details=details)
        return execute
