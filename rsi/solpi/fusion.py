"""Action Fusion: an edit/write and its follow-up command in ONE tool call.

Port of ``src/sol-pi/extensions/action-fusion/{index,then-run,file-queue}.ts``
(spec B3.4). The built-in ``edit`` and ``write`` tools are replaced by versions
with an optional ``then_run = {command, timeout?}`` parameter (each with its own
description, ``EDIT_THEN_RUN_DESCRIPTION`` / ``WRITE_THEN_RUN_DESCRIPTION``):

1. the path is resolved the way ``file-queue.ts:resolveToolPath`` does (unicode
   spaces, ``@``, ``file://``, ``~`` -> the home directory, relative -> the cwd) and
   the per-file FIFO queue is keyed on its ``realpath`` (``canonicalQueueKey``: a
   symlink and its target share one queue);
2. inside that queue the built-in mutation runs; if it fails and ``then_run`` was
   given, the error gains ``[then_run:skipped] The file mutation did not complete
   successfully; the command was not run.``;
3. the file is hashed, the queue yields (``setImmediate`` in the release; here
   :func:`time.sleep(0) <time.sleep>`, which lets other threads - e.g. a concurrent
   writer - run), and hashed again; if the content changed: ``[then_run:skipped]
   target content changed after the fused mutation; the command was not run.``; if
   the file is gone: ``[then_run:skipped] ENOENT: ...; the command was not run.``;
4. the command (any string, including an empty one - the release does not skip
   it) runs through the bash tool: success appends ``[then_run:succeeded]\\n<output>``
   (just ``[then_run:succeeded]`` for empty output); a non-zero exit raises
   ``<mutation output>\\n\\n[then_run:failed]\\n\\n<error>`` (empty parts dropped) - the
   edit is kept.

This removes the decision-free model turn between an edit and its predictable
check ("1 model round-trip avoided").

Environments: an env that resolves tool paths like Pi's built-ins
(``resolves_tool_paths = True`` plus ``cwd`` / ``home``, e.g. AgentWorld) receives the
agent's original ``path``, exactly as the release passes ``editInput`` through; any
other env receives the resolved path so the mutation and the hash guard address the
same file. ``env.realpath`` (optional) replaces :func:`os.path.realpath` for virtual
workspaces.
"""
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
SKIPPED_FAILED_MUTATION = f"{THEN_RUN_SKIPPED} The file mutation did not complete successfully; the command was not run."
SKIPPED_CHANGED = f"{THEN_RUN_SKIPPED} target content changed after the fused mutation; the command was not run."
EDIT_THEN_RUN_DESCRIPTION = ("Command to run next on this file after the edit succeeds — e.g. run, build, "
                             "start/restart, install, or check it; optional timeout in seconds. Skipped if the edit "
                             "fails; a non-zero exit is reported but keeps the edit.")
WRITE_THEN_RUN_DESCRIPTION = ("Command to run next on this file after the write succeeds — e.g. run, build, "
                              "start/restart, install, or check it; optional timeout in seconds. Skipped if the write "
                              "fails; a non-zero exit is reported but keeps the write.")
THEN_RUN_DESCRIPTIONS = {"edit": EDIT_THEN_RUN_DESCRIPTION, "write": WRITE_THEN_RUN_DESCRIPTION}
#: ``file-queue.ts`` UNICODE_SPACES = /[\u00A0\u2000-\u200A\u202F\u205F\u3000]/ (U+200B is NOT included)
UNICODE_SPACES = re.compile(r"[\u00a0\u2000-\u200a\u202f\u205f\u3000]")


def resolve_tool_path(path: str, cwd: Optional[str] = None, home: Optional[str] = None) -> str:
    """``file-queue.ts:resolveToolPath(cwd, filePath)``.

    Unicode spaces become plain spaces and one leading ``@`` is dropped (``normalizeToolPath``); a
    ``file://`` URL becomes its path; ``~`` / ``~/x`` resolve against ``home`` (default: the process home
    directory, ``homedir()``); anything else resolves against ``cwd``. Without a ``cwd`` (an environment
    whose workspace keys are relative paths) the result stays relative but normalised (``./a`` -> ``a``)."""
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
    """``file-queue.ts:canonicalQueueKey``: the realpath of the longest existing prefix plus the missing
    segments (:func:`os.path.realpath` in non-strict mode does exactly that), so a symlink and its target
    share one queue."""
    if realpath is not None:
        return realpath(path)
    if not os.path.isabs(path):        # a relative key of a virtual workspace: nothing on disk to resolve
        return posixpath.normpath(path) if path else path
    return os.path.realpath(path)


def _yield_for_interference() -> None:
    """``then-run.ts`` default ``yieldForInterference`` (``setImmediate``): give other writers a chance."""
    time.sleep(0)


def _validate_then_run(tname: str, then_run, args: dict) -> dict:
    """Pi validates tool arguments against the TypeBox schema before ``execute``:
    ``then_run = {command: string, timeout?: number}``."""
    ok = isinstance(then_run, dict) and isinstance(then_run.get("command"), str) and (
        then_run.get("timeout") is None or (isinstance(then_run.get("timeout"), (int, float))
                                            and not isinstance(then_run.get("timeout"), bool)))
    if not ok:
        raise ToolError(f'Validation failed for tool "{tname}":\n  - then_run: must be an object '
                        f'{{command: string, timeout?: number}}\n\nReceived arguments:\n'
                        f'{json.dumps({**args, "then_run": then_run}, indent=2, default=str)}')
    return then_run


class ActionFusion(Extension):
    """``then_run`` on edit/write (see module docstring). ``yield_hook(rt, path)`` replaces the default
    yield between the two hashes - tests use it to simulate an interleaved writer deterministically."""

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
        """The canonical queue key of a tool ``path`` (resolved, then realpath)."""
        env = rt.env
        target = resolve_tool_path(path, getattr(env, "cwd", None), getattr(env, "home", None))
        return canonical_queue_key(target, getattr(env, "realpath", None))

    def register(self, rt: AgentRuntime) -> None:
        for tname in ("edit", "write"):
            if tname not in rt.tools:
                continue
            base = rt.tools[tname]
            spec = ToolSpec(tname, base.description + " Optional then_run: " + THEN_RUN_DESCRIPTIONS[tname],
                            {**base.parameters, "then_run": "{command: string, timeout?: seconds}"},
                            self._make(tname))
            rt.register_tool(spec, replaces=tname)

    @staticmethod
    def _read(rt: AgentRuntime, target: str) -> Optional[str]:
        return rt.env.read_file(target) if rt.env is not None else None

    def _assert_unchanged(self, rt: AgentRuntime, target: str) -> None:
        """``then-run.ts:assertUnchangedBeforeCommand``: hash, yield, hash again; any failure (a changed or a
        vanished file) becomes ``[then_run:skipped] <reason>; the command was not run.``"""
        def digest() -> str:
            text = self._read(rt, target)
            if text is None:
                raise FileNotFoundError(f"ENOENT: no such file or directory, open '{target}'")
            return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()
        try:
            h1 = digest()
            if self.yield_hook is not None:
                self.yield_hook(rt, target)
            else:
                _yield_for_interference()
            h2 = digest()
            if h1 != h2:
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
            env = rt.env
            raw = args.get("path", "")
            target = resolve_tool_path(raw, getattr(env, "cwd", None), getattr(env, "home", None))
            if not getattr(env, "resolves_tool_paths", False):
                args["path"] = target          # the env keys files by this path; keep mutation and guard aligned
            with self._queue(canonical_queue_key(target, getattr(env, "realpath", None))):
                base = rt.builtin(tname)
                try:
                    result = base.execute(args, rt, cid)
                except ToolError as e:
                    if has_then_run:
                        self.stats["skipped_failed_mutation"] += 1
                        raise ToolError(f"{e}\n\n{SKIPPED_FAILED_MUTATION}") from e
                    raise
                if result.is_error:
                    if has_then_run:
                        self.stats["skipped_failed_mutation"] += 1
                        raise ToolError(f"{result.content}\n\n{SKIPPED_FAILED_MUTATION}")
                    return result
                if not has_then_run:
                    return result
                self._assert_unchanged(rt, target)
                command = then_run["command"]
                self.stats["fused"] += 1
                bash = rt.builtin("bash")
                out = bash.execute({"command": command, "timeout": then_run.get("timeout")}, rt, cid)
                details = {**result.details, "then_run": {"command": command, "exit_error": out.is_error}}
                if out.is_error:
                    self.stats["then_run_failed"] += 1
                    text = "\n\n".join(p for p in (result.content, THEN_RUN_FAILED, out.content) if p)
                    return ToolResult(text, is_error=True, details=details)
                self.stats["then_run_succeeded"] += 1
                block = f"{THEN_RUN_SUCCEEDED}\n{out.content}" if out.content else THEN_RUN_SUCCEEDED
                return ToolResult(f"{result.content}\n{block}" if result.content else block, details=details)
        return execute
