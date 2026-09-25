"""A minimal agent runtime with a Pi-like extension API (spec B9.1 ``AgentRuntime``).

SoL-Pi's mechanisms are Pi *extensions*: they register tools (or replace built-in
ones) and subscribe to lifecycle events; they never patch the base harness. This
runtime reproduces that surface so the four mechanisms (and any new ones) can be
hosted generically:

* ``register_tool(spec, replaces=None)`` - add a tool or replace a built-in one;
* ``on(event, handler)`` for ``session_start``, ``context`` (projection of the
  messages sent to the provider; stored history is never modified),
  ``before_provider_request``, ``tool_result`` (may replace a result before it
  enters history), ``turn_end``, ``agent_settled``, ``input``, ``session_compact``;
* ``compact(custom_instructions)`` (Pi native compaction: summarise everything
  but the most recent ``keep_recent_tokens``), ``abort()``,
  ``send_hidden(text, trigger_turn)``, ``append_entry(type, data)`` (non-context
  session log), ``store`` (the session's private object store, readable by the
  bash tool under ``/.solpi/``);
* ``meter`` - a :class:`~rsi.solpi.meter.TokenMeter` billing every provider request
  with a simulated prefix cache.

The agent itself is a *backend* with ``act(messages, tools, runtime) -> Message``
(an assistant message with text and tool calls): the deterministic
:class:`rsi.domains.agentworld.MockAgent` offline, or an LLM adapter.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Optional, Protocol

from .meter import TokenMeter, estimate_tokens

EVENTS = ("session_start", "context", "before_provider_request", "tool_result", "turn_end", "agent_settled",
          "input", "session_compact")
MESSAGE_OVERHEAD_TOKENS = 4


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    args: dict

    def to_json(self) -> dict:
        return {"id": self.id, "name": self.name, "args": self.args}


@dataclass
class Message:
    role: str                                   # system | user | assistant | tool
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str = ""
    tool_name: str = ""
    is_error: bool = False
    hidden: bool = False
    pad_tokens: int = 0                         # unmaterialised reasoning prose (simulated)
    details: dict = field(default_factory=dict)
    _key: Optional[str] = field(default=None, repr=False, compare=False)
    _tok: Optional[int] = field(default=None, repr=False, compare=False)

    def key(self) -> str:
        if self._key is None:
            h = hashlib.sha1()
            h.update(json.dumps([self.role, self.tool_call_id, self.tool_name, self.is_error, self.pad_tokens,
                                 [c.to_json() for c in self.tool_calls]], sort_keys=True, default=str).encode())
            h.update(self.content.encode("utf-8", "surrogatepass"))
            self._key = h.hexdigest()
        return self._key

    def tokens(self) -> int:
        if self._tok is None:
            args = sum(len(json.dumps(c.args, default=str)) for c in self.tool_calls)
            self._tok = estimate_tokens(self.content) + int((args + 3) // 4) + self.pad_tokens + MESSAGE_OVERHEAD_TOKENS
        return self._tok

    def with_content(self, content: str, **kw: Any) -> "Message":
        return replace(self, content=content, _key=None, _tok=None, **kw)


@dataclass
class ToolResult:
    content: str
    is_error: bool = False
    details: dict = field(default_factory=dict)


class ToolError(Exception):
    """Raised by a tool to report an error result (the message becomes the result text)."""


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict                                    # {param: description}
    execute: Callable[[dict, "AgentRuntime", str], ToolResult]


@dataclass
class ToolResultEvent:
    call: ToolCall
    result: ToolResult


class Backend(Protocol):
    name: str

    def act(self, messages: list[Message], tools: list[ToolSpec], rt: "AgentRuntime") -> Message: ...


class Extension:
    """Base class for opt-in mechanisms. ``register`` wires tools and handlers."""

    name = "extension"

    def __init__(self) -> None:
        self.stats: Counter = Counter()

    def register(self, rt: "AgentRuntime") -> None:  # pragma: no cover - interface
        raise NotImplementedError


@dataclass
class RunResult:
    status: str                      # done | max_turns | context_overflow | error
    turns: int
    final: str = ""
    compactions: int = 0
    overflow: bool = False
    error: Optional[str] = None


def default_summarizer(archived: list[Message], instructions: str, rt: "AgentRuntime") -> str:
    """Deterministic stand-in for Pi's native compaction summariser: keeps the task,
    the agent's own NOTE/PLAN lines (completed work, verification, decisions) and
    the latest plan. Real LLM summarisers can be plugged in via ``AgentRuntime.summarizer``."""
    task = next((m.content for m in archived if m.role == "user" and not m.details.get("compaction")), "")
    prior = [m.content for m in archived if m.details.get("compaction")]
    notes = []
    for m in archived:
        if m.role == "assistant":
            notes.extend(l for l in m.content.splitlines() if l.startswith(("NOTE:", "PLAN:", "DONE:")))
    parts = []
    if prior:
        parts.append(prior[-1])
    elif task:
        parts.append("Goal:\n" + task)
    if notes:
        parts.append("Progress so far:\n" + "\n".join(notes[-40:]))
    if instructions:
        parts.append(f"(summary written under instructions: {instructions})")
    return "\n\n".join(parts)


class AgentRuntime:
    """Hosts one agent session (see module docstring)."""

    def __init__(self, backend: Backend, *, system_prompt: str, meter: TokenMeter, max_turns: int = 80,
                 context_window: int = 200_000, keep_recent_tokens: int = 20_000, auto_compact: bool = True,
                 auto_compact_reserve: int = 16_384,
                 summarizer: Callable[[list[Message], str, "AgentRuntime"], str] = default_summarizer,
                 env: Any = None) -> None:
        self.backend = backend
        self.system_prompt = system_prompt
        self.meter = meter
        self.max_turns = max_turns
        self.context_window = context_window
        self.keep_recent_tokens = keep_recent_tokens
        self.auto_compact = auto_compact
        self.auto_compact_reserve = auto_compact_reserve
        self.summarizer = summarizer
        self.env = env
        self.tools: dict[str, ToolSpec] = {}
        self.replaced: dict[str, ToolSpec] = {}
        self.handlers: dict[str, list[Callable]] = defaultdict(list)
        self.history: list[Message] = []
        self.entries: list[dict] = []
        self.store: dict[str, str] = {}
        self.extensions: list[Extension] = []
        self.turns = 0
        self.compactions = 0
        self.auto_compactions = 0
        self.provider_requests = 0
        self.last_context: list[Message] = []
        self.last_context_tokens = 0
        self.stats: Counter = Counter()
        self._abort = False
        self._pending: list[Message] = []
        self._call_seq = 0

    # ------------------------------------------------------------ extension API
    def register_tool(self, spec: ToolSpec, replaces: Optional[str] = None) -> None:
        if replaces:
            if replaces in self.tools:
                self.replaced[replaces] = self.tools[replaces]
            self.tools.pop(replaces, None)
        self.tools[spec.name] = spec

    def builtin(self, name: str) -> ToolSpec:
        """The original tool an extension replaced (to call through)."""
        return self.replaced.get(name) or self.tools[name]

    def on(self, event: str, handler: Callable) -> None:
        if event not in EVENTS:
            raise ValueError(f"unknown event {event!r}")
        self.handlers[event].append(handler)

    def add_extension(self, ext: Extension) -> None:
        ext.register(self)
        self.extensions.append(ext)

    def abort(self) -> None:
        self._abort = True

    def send_hidden(self, text: str, trigger_turn: bool = True) -> None:
        self._pending.append(Message("user", text, hidden=True, details={"hidden": True, "trigger": trigger_turn}))

    def append_entry(self, type_: str, data: dict) -> None:
        self.entries.append({"type": type_, "data": data, "turn": self.turns})

    def next_call_id(self) -> str:
        self._call_seq += 1
        return f"call_{self._call_seq:04d}"

    # ------------------------------------------------------------ context
    def system_message(self) -> Message:
        lines = [self.system_prompt, "", "Tools:"]
        for t in self.tools.values():
            params = ", ".join(f"{k}: {v}" for k, v in t.parameters.items())
            lines.append(f"- {t.name}({params}): {t.description}")
        return Message("system", "\n".join(lines))

    def project(self) -> list[Message]:
        msgs = [self.system_message()] + list(self.history)
        for h in self.handlers["context"]:
            try:
                out = h(msgs, self)
            except Exception as e:  # noqa: BLE001 - projections fail open
                self.stats["context_handler_error"] += 1
                self.append_entry("context_handler_error", {"error": repr(e)})
                out = None
            if out is not None:
                msgs = out
        return msgs

    def context_tokens(self, msgs: Optional[list[Message]] = None) -> int:
        msgs = self.last_context if msgs is None else msgs
        return int(sum(m.tokens() for m in msgs))

    def emit(self, event: str, *args: Any) -> list[Any]:
        outs = []
        for h in list(self.handlers[event]):
            outs.append(h(*args, self))
        return outs

    # ------------------------------------------------------------ compaction
    def cut_index(self) -> int:
        """Index of the first kept history message (Pi ``findCutPoint``): keep at least
        ``keep_recent_tokens`` of recent history, cut at an assistant-message boundary."""
        acc, i = 0, len(self.history)
        while i > 0 and acc < self.keep_recent_tokens:
            i -= 1
            acc += self.history[i].tokens()
        while i > 0 and self.history[i].role == "tool":
            i -= 1
        return i

    def compactable(self) -> bool:
        return self.cut_index() > 1

    def compact(self, custom_instructions: str = "", *, from_extension: bool = False) -> Optional[str]:
        cut = self.cut_index()
        if cut <= 1:
            return None
        archived, kept = self.history[:cut], self.history[cut:]
        # the summarisation request reads the cached conversation prefix and writes the summary
        prefix = [self.system_message()] + archived
        self.meter.request([m.key() for m in prefix], [m.tokens() for m in prefix], role="compaction", scope="main")
        summary = self.summarizer(archived, custom_instructions, self)
        self.meter.add_output(estimate_tokens(summary), role="compaction")
        self.meter.reset_cache("main")    # the next request after the rewrite starts from a fresh prefix
        self.history = [Message("user", "[compacted context summary]\n" + summary,
                                details={"compaction": True})] + kept
        self.compactions += 1
        self.emit("session_compact", {"from_extension": from_extension, "summary": summary,
                                      "archived_tokens": sum(m.tokens() for m in archived)})
        return summary

    # ------------------------------------------------------------ the loop
    def run(self, task: str) -> RunResult:
        self.emit("session_start")
        self.history.append(Message("user", task))
        status, final, error = "max_turns", "", None
        while self.turns < self.max_turns:
            msgs = self.project()
            ctx = self.context_tokens(msgs)
            if self.auto_compact and ctx >= self.context_window - self.auto_compact_reserve and self.compactable():
                self.compact("")
                self.auto_compactions += 1
                msgs = self.project()
                ctx = self.context_tokens(msgs)
            if ctx > self.context_window:
                return RunResult("context_overflow", self.turns, compactions=self.compactions, overflow=True)
            self.last_context, self.last_context_tokens = msgs, ctx
            self.emit("before_provider_request", msgs)
            self.meter.request([m.key() for m in msgs], [m.tokens() for m in msgs], role="main")
            self.provider_requests += 1
            try:
                reply = self.backend.act(msgs, list(self.tools.values()), self)
            except Exception as e:  # noqa: BLE001
                return RunResult("error", self.turns, compactions=self.compactions, error=f"{type(e).__name__}: {e}")
            self.meter.add_output(reply.tokens(), role="main")
            self.history.append(reply)
            self.turns += 1
            if not reply.tool_calls:
                status, final = "done", reply.content
                break
            results = []
            for call in reply.tool_calls:
                spec = self.tools.get(call.name)
                if spec is None:
                    res = ToolResult(f"Unknown tool: {call.name}", is_error=True)
                else:
                    try:
                        res = spec.execute(dict(call.args), self, call.id)
                    except ToolError as e:
                        res = ToolResult(str(e), is_error=True)
                    except Exception as e:  # noqa: BLE001
                        res = ToolResult(f"{type(e).__name__}: {e}", is_error=True)
                ev = ToolResultEvent(call, res)
                for h in list(self.handlers["tool_result"]):
                    try:
                        out = h(ev, self)
                    except Exception as e:  # noqa: BLE001 - result handlers fail open
                        self.append_entry("tool_result_handler_error", {"error": repr(e)})
                        out = None
                    if out is not None:
                        ev.result = out
                msg = Message("tool", ev.result.content, tool_call_id=call.id, tool_name=call.name,
                              is_error=ev.result.is_error, details=dict(ev.result.details))
                self.history.append(msg)
                results.append(msg)
            self.emit("turn_end", reply, results)
            if self._abort:
                self._abort = False
                self.emit("agent_settled")
            if self._pending:
                self.history.extend(self._pending)
                self._pending.clear()
        return RunResult(status, self.turns, final=final, compactions=self.compactions)


# ------------------------------------------------------------------ built-in tools
def builtin_tools(env: Any) -> list[ToolSpec]:
    """Pi's base tools over an environment exposing ``read/write/edit/bash``."""

    def _read(args, rt, cid):
        return env.tool_read(args.get("path", ""), rt)

    def _write(args, rt, cid):
        return env.tool_write(args.get("path", ""), args.get("content", ""), rt)

    def _edit(args, rt, cid):
        return env.tool_edit(args.get("path", ""), args.get("old", ""), args.get("new", ""), rt)

    def _bash(args, rt, cid):
        return env.tool_bash(args.get("command", ""), rt)

    return [
        ToolSpec("read", "Read a file.", {"path": "file path"}, _read),
        ToolSpec("write", "Create or overwrite a file.", {"path": "file path", "content": "full content"}, _write),
        ToolSpec("edit", "Replace one exact, unique occurrence of text in a file.",
                 {"path": "file path", "old": "exact text", "new": "replacement"}, _edit),
        ToolSpec("bash", "Run a shell command; non-zero exit is reported as an error.",
                 {"command": "command line"}, _bash),
    ]
