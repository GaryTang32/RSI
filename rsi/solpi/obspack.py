"""ObservationPack: stop replaying large tool results, keep exact access by handle.

Port of ``src/sol-pi/extensions/observation-pack/{index,observation,ledger}.ts``
(spec B3.4 / B4.3). On every provider request (``context`` projection - stored
history is never modified):

* a pure-text, non-error tool result larger than ``THRESHOLD_BYTES`` (10 KiB) that
  is not an EPR receipt becomes an :class:`Observation` with id
  ``"obs_" + sha256(tool \\0 call_id \\0 sha256(text))[:24]``, stored
  content-addressed (an existing object is reused only if size and hash match);
* the first ``FULL_SENDS`` (2) provider requests carry it in full ("full" ledger
  entry); later ones get a stable placeholder with a head/tail excerpt of whole
  lines (``PLACEHOLDER_EXCERPT_BYTES`` = 1024, split 512/512) and the recall
  instruction ("placeholder" entry with ``removedTokens``);
* ``obs_recall(id, offset)`` pages the stored bytes (<= 16,384 - 512 header bytes,
  <= 400 - 2 lines, UTF-8 safe) with ``next_offset`` / ``eof``;
* any error fails open: the original message is kept.

``full_sends`` / ``excerpt_bytes`` / ``head_frac`` are exposed for the sweep the
blog reports (V0-V7).
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Optional

from .meter import estimate_tokens
from .runtime import AgentRuntime, Extension, Message, ToolError, ToolResult, ToolSpec

THRESHOLD_BYTES = 10 * 1024
FULL_SENDS = 2
PLACEHOLDER_EXCERPT_BYTES = 1024
RECALL_MAX_BYTES = 16 * 1024
RECALL_MAX_LINES = 400
RECALL_HEADER_RESERVE_BYTES = 512
RECALL_HEADER_LINES = 2
OBS_ID = re.compile(r"^obs_[a-f0-9]{24}$")
RECEIPT_PREFIX = "sol_pi_evidence_receipt_v1"
STORE_PREFIX = "/.solpi/observation-pack/objects/"


def sha(text: str | bytes) -> str:
    return hashlib.sha256(text.encode() if isinstance(text, str) else text).hexdigest()


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
    lines = text.splitlines(keepends=True)
    sel, used = [], 0
    seq = reversed(lines) if from_end else iter(lines)
    for line in seq:
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
    return {"text": chunk.decode("utf-8"), "bytes": len(chunk), "lines": count_lines(chunk.decode("utf-8")),
            "next_offset": nxt, "eof": nxt >= len(data)}


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
        self.fail_store = False           # test hook: inject storage errors

    def register(self, rt: AgentRuntime) -> None:
        rt.register_tool(ToolSpec("obs_recall", "Read a stored large tool result by observation id and byte offset.",
                                  {"id": "observation id from a placeholder", "offset": "byte offset, default 0"},
                                  self._recall))
        rt.on("context", self._context)

    # ---- storage
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

    # ---- projection
    def _context(self, msgs: list[Message], rt: AgentRuntime) -> list[Message]:
        projected = list(msgs)
        prior = [0] * len(msgs)
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
                if prev < self.full_sends:
                    self.ledger.append({"event": "full", "id": obs.id, "request": request, "tool": obs.tool_name,
                                        "originalBytes": obs.bytes, "originalLines": obs.lines,
                                        "originalTokens": obs.tokens, "contentHash": obs.content_hash})
                    self.sent[obs.id] = prev + 1
                    continue
                ph = placeholder_for(obs, full_sends=self.full_sends, excerpt_bytes=self.excerpt_bytes,
                                     head_frac=self.head_frac)
                ph_tokens = estimate_tokens(ph)
                removed = max(0, obs.tokens - ph_tokens)
                self.ledger.append({"event": "placeholder", "id": obs.id, "request": request, "sendNumber": prev + 1,
                                    "tool": obs.tool_name, "originalBytes": obs.bytes, "originalTokens": obs.tokens,
                                    "placeholderBytes": len(ph.encode()), "placeholderTokens": ph_tokens,
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

    # ---- recall tool
    def _recall(self, args: dict, rt: AgentRuntime, cid: str) -> ToolResult:
        oid = str(args.get("id", ""))
        if not OBS_ID.match(oid):
            raise ToolError(f"Unknown observation id: {oid}")
        text = rt.store.get(STORE_PREFIX + oid + ".txt")
        if text is None:
            raise ToolError(f"Unknown observation id: {oid}")
        offset = int(args.get("offset") or 0)
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
        return ToolResult(content, details={"id": oid, "offset": offset, "next_offset": chunk["next_offset"],
                                            "eof": chunk["eof"], "obs_recall": True})
