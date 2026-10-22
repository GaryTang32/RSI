"""A minimal agent runtime with Pi's extension API: tools, lifecycle hooks, compaction and a token meter."""
from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Optional, Protocol

from .meter import TokenMeter, estimate_tokens
from .pi_compaction import summarization_request_tokens

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
    """Raised by a tool to report an error result."""


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
    """Base class for opt-in mechanisms; ``register`` wires tools and handlers."""

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
    """Deterministic stand-in for Pi's summariser: the task (or prior summary) and the agent's NOTE/PLAN/DONE lines."""
    task = next((m.content for m in archived if m.role == "user" and not m.details.get("compaction")), "")
    prior = [m.content for m in archived if m.details.get("compaction")]
    notes = [l for m in archived if m.role == "assistant" for l in m.content.splitlines()
             if l.startswith(("NOTE:", "PLAN:", "DONE:"))]
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
    """Hosts one agent session; the backend's ``act(messages, tools, runtime)`` returns the assistant message."""

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

    @property
    def aborted(self) -> bool:
        """An abort was requested during the current turn (``context.signal.aborted``)."""
        return self._abort

    def send_hidden(self, text: str, trigger_turn: bool = True) -> None:
        self._pending.append(Message("user", text, hidden=True, details={"hidden": True, "trigger": trigger_turn}))

    def append_entry(self, type_: str, data: dict) -> None:
        self.entries.append({"type": type_, "data": data, "turn": self.turns})

    def next_call_id(self) -> str:
        self._call_seq += 1
        return f"call_{self._call_seq:04d}"

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

    def context_usage(self) -> Optional[int]:
        """Pi's ``getContextUsage().tokens``: last valid reply's usage + later estimates (``None``: no reply)."""
        for i in range(len(self.history) - 1, -1, -1):
            m = self.history[i]
            if m.role != "assistant":
                continue
            u = m.details.get("usage_tokens")
            if (not isinstance(u, int) or u <= 0 or m.details.get("usage_compactions") != self.compactions
                    or m.details.get("stop_reason") in ("aborted", "error")):
                continue
            return int(u + sum(x.tokens() for x in self.history[i + 1:]))
        return None

    def emit(self, event: str, *args: Any) -> list[Any]:
        return [h(*args, self) for h in list(self.handlers[event])]

    def should_auto_compact(self) -> bool:
        """Pi's ``shouldCompact``: stored-context tokens strictly above ``context_window - auto_compact_reserve``."""
        if not self.auto_compact:
            return False
        est = self.context_usage()
        if est is None:
            est = self.context_tokens([self.system_message()] + list(self.history))
        return est > self.context_window - self.auto_compact_reserve and self.compactable()

    def cut_index(self) -> int:
        """Pi's ``findCutPoint``: first kept message, keeping >= ``keep_recent_tokens`` and no leading tool result."""
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
        # Pi sends the summarisation request with cacheRetention "none": it shares no prefix with the conversation
        self.meter.uncached(summarization_request_tokens(archived, custom_instructions, MESSAGE_OVERHEAD_TOKENS),
                            role="compaction")
        summary = self.summarizer(archived, custom_instructions, self)
        self.meter.add_output(estimate_tokens(summary), role="compaction")
        self.history = [Message("user", "[compacted context summary]\n" + summary,
                                details={"compaction": True})] + kept
        self.compactions += 1
        self.emit("session_compact", {"from_extension": from_extension, "summary": summary,
                                      "archived_tokens": sum(m.tokens() for m in archived)})
        return summary

    def run(self, task: str) -> RunResult:
        self.emit("session_start")
        self.history.append(Message("user", task))
        status, final = "max_turns", ""
        while self.turns < self.max_turns:
            if self.should_auto_compact():
                self.compact("")
                self.auto_compactions += 1
            msgs = self.project()               # exactly one projection per request: handlers have side effects
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
            reply.details = {**reply.details, "usage_tokens": int(ctx + reply.tokens()),
                             "usage_compactions": self.compactions}
            self.history.append(reply)
            self.turns += 1
            if not reply.tool_calls:
                status, final = "done", reply.content
                break
            results = []
            for call in reply.tool_calls:
                msg = self._result_message(call, self._call_tool(call))
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

    def _call_tool(self, call: ToolCall) -> ToolResult:
        spec = self.tools.get(call.name)
        if spec is None:
            return ToolResult(f"Unknown tool: {call.name}", is_error=True)
        try:
            return spec.execute(dict(call.args), self, call.id)
        except ToolError as e:
            return ToolResult(str(e), is_error=True)
        except Exception as e:  # noqa: BLE001
            return ToolResult(f"{type(e).__name__}: {e}", is_error=True)

    def _result_message(self, call: ToolCall, res: ToolResult) -> Message:
        """The history message of a result after the ``tool_result`` handlers (which fail open) replaced it."""
        ev = ToolResultEvent(call, res)
        for h in list(self.handlers["tool_result"]):
            try:
                out = h(ev, self)
            except Exception as e:  # noqa: BLE001
                self.append_entry("tool_result_handler_error", {"error": repr(e)})
                continue
            if out is not None:
                ev.result = out
        return Message("tool", ev.result.content, tool_call_id=call.id, tool_name=call.name,
                       is_error=ev.result.is_error, details=dict(ev.result.details))


def builtin_tools(env: Any) -> list[ToolSpec]:
    """Pi's base tools over an environment exposing ``tool_read/tool_write/tool_edit/tool_bash``."""
    return [
        ToolSpec("read", "Read a file.", {"path": "file path"},
                 lambda a, rt, cid: env.tool_read(a.get("path", ""), rt)),
        ToolSpec("write", "Create or overwrite a file.", {"path": "file path", "content": "full content"},
                 lambda a, rt, cid: env.tool_write(a.get("path", ""), a.get("content", ""), rt)),
        ToolSpec("edit", "Replace one exact, unique occurrence of text in a file.",
                 {"path": "file path", "old": "exact text", "new": "replacement"},
                 lambda a, rt, cid: env.tool_edit(a.get("path", ""), a.get("old", ""), a.get("new", ""), rt)),
        ToolSpec("bash", "Run a shell command; non-zero exit is reported as an error.", {"command": "command line"},
                 lambda a, rt, cid: env.tool_bash(a.get("command", ""), rt)),
    ]
