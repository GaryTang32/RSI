"""Action Fusion: an edit/write and its follow-up command in one tool call (``then_run``, with a hash guard)."""
from __future__ import annotations

import hashlib
import json
import os
import posixpath
import re
import threading
import time
from typing import Callable, Optional
from urllib.parse import unquote, urlparse

from .runtime import AgentRuntime, Extension, ToolError, ToolResult, ToolSpec

THEN_RUN_SUCCEEDED = "[then_run:succeeded]"
THEN_RUN_FAILED = "[then_run:failed]"
THEN_RUN_SKIPPED = "[then_run:skipped]"
SKIPPED_FAILED_MUTATION = (f"{THEN_RUN_SKIPPED} The file mutation did not complete successfully; "
                           "the command was not run.")
EDIT_THEN_RUN_DESCRIPTION = ("Command to run next on this file after the edit succeeds — e.g. run, build, "
                             "start/restart, install, or check it; optional timeout in seconds. Skipped if the edit "
                             "fails; a non-zero exit is reported but keeps the edit.")
WRITE_THEN_RUN_DESCRIPTION = ("Command to run next on this file after the write succeeds — e.g. run, build, "
                              "start/restart, install, or check it; optional timeout in seconds. Skipped if the write "
                              "fails; a non-zero exit is reported but keeps the write.")
THEN_RUN_DESCRIPTIONS = {"edit": EDIT_THEN_RUN_DESCRIPTION, "write": WRITE_THEN_RUN_DESCRIPTION}
UNICODE_SPACES = re.compile(r"[\u00a0\u2000-\u200a\u202f\u205f\u3000]")   # file-queue.ts (U+200B is not one)


def resolve_tool_path(path: str, cwd: Optional[str] = None, home: Optional[str] = None) -> str:
    """``file-queue.ts:resolveToolPath``; without a ``cwd`` the result stays relative (but normalised)."""
    p = UNICODE_SPACES.sub(" ", str(path or ""))
    if p.startswith("@"):
        p = p[1:]
    if p.startswith("file://"):
        p = unquote(urlparse(p).path)
    home = home if home is not None else os.path.expanduser("~")
    if p == "~":
        return posixpath.normpath(home)
    if p.startswith("~/"):
        return posixpath.normpath(posixpath.join(home, p[2:]))
    if cwd is not None:
        return posixpath.normpath(posixpath.join(cwd, p))
    return posixpath.normpath(p) if p else p


def canonical_queue_key(path: str, realpath: Optional[Callable[[str], str]] = None) -> str:
    """``file-queue.ts:canonicalQueueKey``: a symlink and its target share one queue."""
    if realpath is not None:
        return realpath(path)
    if not os.path.isabs(path):        # a virtual workspace key: nothing on disk to resolve
        return posixpath.normpath(path) if path else path
    return os.path.realpath(path)


def _resolve(env, path: str) -> str:
    return resolve_tool_path(path, getattr(env, "cwd", None), getattr(env, "home", None))


def _yield_for_interference() -> None:
    """``then-run.ts`` ``yieldForInterference`` (``setImmediate``): give other writers a chance."""
    time.sleep(0)


def _validate_then_run(tname: str, then_run, args: dict) -> dict:
    """Pi's schema check before ``execute``: ``then_run = {command: string, timeout?: number}``."""
    timeout = then_run.get("timeout") if isinstance(then_run, dict) else None
    ok = isinstance(then_run, dict) and isinstance(then_run.get("command"), str) and (
        timeout is None or (isinstance(timeout, (int, float)) and not isinstance(timeout, bool)))
    if not ok:
        raise ToolError(f'Validation failed for tool "{tname}":\n  - then_run: must be an object '
                        f'{{command: string, timeout?: number}}\n\nReceived arguments:\n'
                        f'{json.dumps({**args, "then_run": then_run}, indent=2, default=str)}')
    return then_run


class ActionFusion(Extension):
    """``then_run`` on edit/write; ``yield_hook(rt, path)`` replaces the yield between the two hashes."""

    name = "action_fusion"

    def __init__(self, yield_hook: Optional[Callable[[AgentRuntime, str], None]] = None) -> None:
        super().__init__()
        self.yield_hook = yield_hook
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    def _queue(self, key: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(key, threading.Lock())

    def queue_key(self, rt: AgentRuntime, path: str) -> str:
        return canonical_queue_key(_resolve(rt.env, path), getattr(rt.env, "realpath", None))

    def register(self, rt: AgentRuntime) -> None:
        for tname in ("edit", "write"):
            if tname not in rt.tools:
                continue
            base = rt.tools[tname]
            spec = ToolSpec(tname, base.description + " Optional then_run: " + THEN_RUN_DESCRIPTIONS[tname],
                            {**base.parameters, "then_run": "{command: string, timeout?: seconds}"},
                            self._make(tname))
            rt.register_tool(spec, replaces=tname)

    def _assert_unchanged(self, rt: AgentRuntime, target: str) -> None:
        """``then-run.ts:assertUnchangedBeforeCommand``: hash, yield, hash again; a change or ENOENT skips the run."""
        def digest() -> str:
            text = rt.env.read_file(target) if rt.env is not None else None
            if text is None:
                raise FileNotFoundError(f"ENOENT: no such file or directory, open '{target}'")
            return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()
        try:
            before = digest()
            if self.yield_hook is not None:
                self.yield_hook(rt, target)
            else:
                _yield_for_interference()
            if digest() != before:
                raise RuntimeError("target content changed after the fused mutation")
        except Exception as e:  # noqa: BLE001 - every guard failure skips the command
            self.stats["skipped_changed" if isinstance(e, RuntimeError) else "skipped_unreadable"] += 1
            raise ToolError(f"{THEN_RUN_SKIPPED} {e}; the command was not run.") from e

    def _make(self, tname: str):
        def execute(args: dict, rt: AgentRuntime, cid: str) -> ToolResult:
            has_then_run = "then_run" in args and args.get("then_run") is not None
            then_run = args.pop("then_run", None)
            if has_then_run:
                then_run = _validate_then_run(tname, then_run, args)
            target = _resolve(rt.env, args.get("path", ""))
            if not getattr(rt.env, "resolves_tool_paths", False):
                args["path"] = target          # the env keys files by this path; keep mutation and guard aligned
            with self._queue(canonical_queue_key(target, getattr(rt.env, "realpath", None))):
                try:
                    result = rt.builtin(tname).execute(args, rt, cid)
                except ToolError as e:
                    if has_then_run:
                        self.stats["skipped_failed_mutation"] += 1
                        raise ToolError(f"{e}\n\n{SKIPPED_FAILED_MUTATION}") from e
                    raise
                if not has_then_run:
                    return result
                if result.is_error:
                    self.stats["skipped_failed_mutation"] += 1
                    raise ToolError(f"{result.content}\n\n{SKIPPED_FAILED_MUTATION}")
                self._assert_unchanged(rt, target)
                command = then_run["command"]
                self.stats["fused"] += 1
                out = rt.builtin("bash").execute({"command": command, "timeout": then_run.get("timeout")}, rt, cid)
                details = {**result.details, "then_run": {"command": command, "exit_error": out.is_error}}
                if out.is_error:
                    self.stats["then_run_failed"] += 1
                    text = "\n\n".join(p for p in (result.content, THEN_RUN_FAILED, out.content) if p)
                    return ToolResult(text, is_error=True, details=details)
                self.stats["then_run_succeeded"] += 1
                block = f"{THEN_RUN_SUCCEEDED}\n{out.content}" if out.content else THEN_RUN_SUCCEEDED
                return ToolResult(f"{result.content}\n{block}" if result.content else block, details=details)
        return execute
