"""ObservationPack: after two full sends a large tool result becomes a placeholder; ``obs_recall`` pages it back."""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Optional

from .reducer import REDUCER_RECEIPT_PREFIX as RECEIPT_PREFIX
from .reducer import utf16_len
from .runtime import AgentRuntime, Extension, Message, ToolError, ToolResult, ToolSpec

THRESHOLD_BYTES = 10 * 1024
FULL_SENDS = 2
PLACEHOLDER_EXCERPT_BYTES = 1024
RECALL_MAX_BYTES = 16 * 1024
RECALL_MAX_LINES = 400
RECALL_HEADER_RESERVE_BYTES = 512
RECALL_HEADER_LINES = 2
OBS_ID = re.compile(r"^obs_[a-f0-9]{24}$")
STORE_PREFIX = "/.solpi/observation-pack/objects/"


def sha(text: str | bytes) -> str:
    return hashlib.sha256(text.encode() if isinstance(text, str) else text).hexdigest()


def estimate_tokens(text: str) -> int:
    """``observation.ts:estimateTokens``: ``ceil(text.length / 4)`` in UTF-16 units."""
    return int(math.ceil(utf16_len(text or "") / 4))


def count_lines(text: str) -> int:
    if not text:
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)


@dataclass(frozen=True)
class Observation:
    id: str
    content_hash: str
    path: str
    tool_name: str
    text: str
    bytes: int
    lines: int
    tokens: int


def create_observation(msg: Message, threshold: int = THRESHOLD_BYTES) -> Optional[Observation]:
    text = msg.content
    if any(line == RECEIPT_PREFIX for line in text.split("\n")):
        return None
    nbytes = len(text.encode("utf-8"))
    if nbytes <= threshold:
        return None
    ch = sha(text)
    oid = "obs_" + sha(f"{msg.tool_name}\0{msg.tool_call_id}\0{ch}")[:24]
    return Observation(oid, ch, STORE_PREFIX + oid + ".txt", msg.tool_name, text, nbytes, count_lines(text),
                       estimate_tokens(text))


def complete_line_excerpt(text: str, budget: int, from_end: bool) -> str:
    """``observation.ts:completeLineExcerpt``: whole ``\\n``-terminated lines within ``budget`` UTF-8 bytes."""
    lines = re.split(r"(?<=\n)", text)
    sel, used = [], 0
    for line in reversed(lines) if from_end else lines:
        n = len(line.encode("utf-8"))
        if used + n > budget:
            break
        sel.append(line)
        used += n
    return "".join(reversed(sel) if from_end else sel)


def placeholder_for(obs: Observation, *, full_sends: int = FULL_SENDS, excerpt_bytes: int = PLACEHOLDER_EXCERPT_BYTES,
                    head_frac: float = 0.5) -> str:
    head_budget = int(excerpt_bytes * head_frac)
    tail_budget = excerpt_bytes - head_budget
    head = complete_line_excerpt(obs.text, head_budget, False) if head_budget > 0 else ""
    tail = complete_line_excerpt(obs.text, tail_budget, True) if tail_budget > 0 else ""
    return "\n".join([
        f"[large tool result replaced after its first {full_sends} provider requests]",
        f"id: {obs.id}",
        f"tool: {obs.tool_name}",
        f"original_bytes: {obs.bytes}",
        f"original_lines: {obs.lines}",
        f"estimated_tokens: {obs.tokens}",
        f'retrieve: call obs_recall with {{"id":"{obs.id}","offset":0}}; continue with returned next_offset',
        f"[first complete lines, up to {head_budget} bytes]",
        head,
        f"[middle omitted; last complete lines, up to {tail_budget} bytes]",
        tail,
        f"[{obs.bytes} original bytes omitted]",
    ])


def read_recall_chunk(data: bytes, offset: int, max_bytes: int, max_lines: int) -> dict:
    if offset < 0:
        raise ToolError(f"Offset {offset} must be a non-negative integer")
    if offset > len(data):
        raise ToolError(f"Offset {offset} exceeds observation size {len(data)}")
    buf = data[offset:offset + max_bytes + 4]
    end = min(len(buf), max_bytes)
    nl = 0
    for i in range(end):
        if buf[i] == 0x0A:
            nl += 1
            if nl == max_lines:
                end = i + 1
                break
    while 0 < end < len(buf) and (buf[end] & 0xC0) == 0x80:     # never split a UTF-8 sequence
        end -= 1
    chunk = buf[:end]
    nxt = offset + len(chunk)
    text = chunk.decode("utf-8")
    return {"text": text, "bytes": len(chunk), "lines": count_lines(text), "next_offset": nxt, "eof": nxt >= len(data)}


class ObservationPack(Extension):
    name = "observation_pack"

    def __init__(self, *, full_sends: int = FULL_SENDS, excerpt_bytes: int = PLACEHOLDER_EXCERPT_BYTES,
                 head_frac: float = 0.5, threshold_bytes: int = THRESHOLD_BYTES) -> None:
        super().__init__()
        self.full_sends = full_sends
        self.excerpt_bytes = excerpt_bytes
        self.head_frac = head_frac
        self.threshold = threshold_bytes
        self.sent: dict[str, int] = {}
        self.ledger: list[dict] = []
        self.fail_store = False           # fault injection

    def register(self, rt: AgentRuntime) -> None:
        rt.register_tool(ToolSpec("obs_recall", "Read a stored large tool result by observation id and byte offset.",
                                  {"id": "observation id from a placeholder", "offset": "byte offset, default 0"},
                                  self._recall))
        rt.on("context", self._context)

    def _ensure_stored(self, rt: AgentRuntime, obs: Observation) -> None:
        if self.fail_store:
            raise OSError("injected storage failure")
        existing = rt.store.get(obs.path)
        if existing is None:
            rt.store[obs.path] = obs.text
            return
        if len(existing.encode("utf-8")) != obs.bytes:
            raise OSError(f"Content-addressed observation size mismatch for {obs.id}")
        if sha(existing) != obs.content_hash:
            raise OSError(f"Content-addressed observation hash mismatch for {obs.id}")

    def _context(self, msgs: list[Message], rt: AgentRuntime) -> list[Message]:
        """Projection only: stored history is never modified; any error keeps the original message."""
        projected = list(msgs)
        prior = [0] * len(msgs)            # prior[i] = assistant replies after message i (requests it was sent in)
        count = 0
        for i in range(len(msgs) - 1, -1, -1):
            prior[i] = count
            if msgs[i].role == "assistant":
                count += 1
        request = count + 1
        for i, m in enumerate(msgs):
            if m.role != "tool" or m.is_error or not m.content:
                continue
            try:
                obs = create_observation(m, self.threshold)
                if obs is None:
                    continue
                self._ensure_stored(rt, obs)
                prev = self.sent.get(obs.id, prior[i])
                base = {"id": obs.id, "request": request}
                orig = {"originalBytes": obs.bytes, "originalLines": obs.lines, "originalTokens": obs.tokens}
                if prev < self.full_sends:
                    self.ledger.append({"event": "full", **base, "tool": obs.tool_name, **orig,
                                        "contentHash": obs.content_hash})
                    self.sent[obs.id] = prev + 1
                    continue
                ph = placeholder_for(obs, full_sends=self.full_sends, excerpt_bytes=self.excerpt_bytes,
                                     head_frac=self.head_frac)
                ph_tokens = estimate_tokens(ph)
                removed = max(0, obs.tokens - ph_tokens)
                self.ledger.append({"event": "placeholder", **base, "sendNumber": prev + 1, "tool": obs.tool_name,
                                    **orig, "placeholderBytes": len(ph.encode()), "placeholderTokens": ph_tokens,
                                    "removedTokens": removed})
                if prev == self.full_sends:
                    self.stats["packed"] += 1
                self.stats["placeholder_sends"] += 1
                self.stats["removed_tokens"] += removed
                self.sent[obs.id] = prev + 1
                projected[i] = m.with_content(ph, details={**m.details, "observation_pack": obs.id})
            except Exception:  # noqa: BLE001 - "must never cost the agent its observation"
                self.stats["fail_open"] += 1
                projected[i] = m
        return projected

    @staticmethod
    def _validate_recall_args(args: dict) -> tuple[str, int]:
        """Pi's schema check before ``execute``: ``{id: string, offset?: integer >= 0}``."""
        errors = []
        oid, offset = args.get("id"), args.get("offset")
        if not isinstance(oid, str):
            errors.append("  - id: must be string")
        if offset is not None and (isinstance(offset, bool) or not isinstance(offset, (int, float))
                                   or offset != int(offset)):
            errors.append("  - offset: must be integer")
        elif offset is not None and offset < 0:
            errors.append("  - offset: must be >= 0")
        if errors:
            raise ToolError('Validation failed for tool "obs_recall":\n' + "\n".join(errors) +
                            "\n\nReceived arguments:\n" + json.dumps(args, indent=2, default=str))
        return oid, int(offset or 0)

    def _recall(self, args: dict, rt: AgentRuntime, cid: str) -> ToolResult:
        oid, offset = self._validate_recall_args(args)
        text = rt.store.get(STORE_PREFIX + oid + ".txt") if OBS_ID.match(oid) else None
        if text is None:
            raise ToolError(f"Unknown observation id: {oid}")
        chunk = read_recall_chunk(text.encode("utf-8"), offset, RECALL_MAX_BYTES - RECALL_HEADER_RESERVE_BYTES,
                                  RECALL_MAX_LINES - RECALL_HEADER_LINES)
        header = (f"[obs_recall id={oid} offset={offset} next_offset={chunk['next_offset']} eof={chunk['eof']}]\n"
                  f"[chunk_bytes={chunk['bytes']} chunk_lines={chunk['lines']}; use next_offset to continue]")
        content = header + "\n" + chunk["text"]
        if len(content.encode("utf-8")) > RECALL_MAX_BYTES or count_lines(content) > RECALL_MAX_LINES:
            raise ToolError("Recall output exceeded its hard limit")
        self.ledger.append({"event": "recall", "id": oid, "offset": offset, "bytes": chunk["bytes"],
                            "lines": chunk["lines"], "nextOffset": chunk["next_offset"], "eof": chunk["eof"]})
        self.stats["recalls"] += 1
        return ToolResult(content, details={"id": oid, "offset": offset, "bytes": chunk["bytes"],
                                            "lines": chunk["lines"], "nextOffset": chunk["next_offset"],
                                            "next_offset": chunk["next_offset"], "eof": chunk["eof"],
                                            "obs_recall": True})
