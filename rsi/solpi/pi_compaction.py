"""Pi 0.85.1 native-compaction *request* (what the summarisation call sends and how it is billed).

Port of ``@earendil-works/pi-coding-agent@0.85.1`` ``dist/core/compaction/{compaction,utils}.js``
(``generateSummaryWithUsage``, ``serializeConversation``, ``buildSummarizationContext``,
``completeSummarization``). The summarisation call is NOT a continuation of the agent's
conversation:

* the messages to summarise are serialised to one text (``[User]: ...``, ``[Assistant]: ...``,
  ``[Assistant tool calls]: name(k=json, ...)``, ``[Tool result]: ...``), every tool result cut to
  ``TOOL_RESULT_MAX_CHARS`` = 2,000 characters (``truncateForSummary``);
* the request is ``{systemPrompt: SUMMARIZATION_SYSTEM_PROMPT, messages: [one user message]}`` with
  ``<conversation>...</conversation>``, an optional ``<previous-summary>`` block and the
  (update) summarisation prompt, plus ``"\\n\\nAdditional focus: " + customInstructions``;
* it is sent with ``cacheRetention: "none"`` ("Avoid cache writes for one-off summaries"), so
  it shares no prompt-cache prefix with the agent's conversation and all of its input is
  billed as uncached input.

The prompt texts below are copied verbatim from Pi 0.85.1 (MIT). The summary *content* is
still produced by :func:`rsi.solpi.runtime.default_summarizer` (or an LLM summariser); this
module only determines the request's size and billing.

Known simplification (not ported): Pi's split-turn path. When ``findCutPoint`` cuts inside a turn
(``isSplitTurn``: the first kept entry is not a user message), Pi's ``compact()`` sends a
``TURN_PREFIX_SUMMARIZATION_PROMPT`` request over the turn's prefix (about 110 prompt tokens, no
``<previous-summary>`` block, half the output budget), plus a separate history request only when
messages precede the turn, and it appends a read/modified-files list (``formatFileOperations``) to
the summary. In single-prompt AgentWorld tasks most auto-compactions are split turns. We always send
ONE summarisation/update request over the archived messages instead. Both requests are uncached and
serialise the same messages, so the billing difference is the prompt/previous-summary overhead
(order 10^2 tokens per compaction, against serialised conversations of order 10^4) plus the short
file list the next request writes to cache.
"""
from __future__ import annotations

import json
import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from .runtime import Message

TOOL_RESULT_MAX_CHARS = 2000
SUMMARY_PREFIX = "[compacted context summary]\n"

SUMMARIZATION_SYSTEM_PROMPT = (
    "You are a context summarization assistant. Your task is to read a conversation between a user and an AI "
    "assistant, then produce a structured summary following the exact format specified.\n\n"
    "Do NOT continue the conversation. Do NOT respond to any questions in the conversation. ONLY output the "
    "structured summary.")

SUMMARIZATION_PROMPT = """The messages above are a conversation to summarize. Create a structured context checkpoint summary that another LLM will use to continue the work.

Use this EXACT format:

## Goal
[What is the user trying to accomplish? Can be multiple items if the session covers different tasks.]

## Constraints & Preferences
- [Any constraints, preferences, or requirements mentioned by user]
- [Or "(none)" if none were mentioned]

## Progress
### Done
- [x] [Completed tasks/changes]

### In Progress
- [ ] [Current work]

### Blocked
- [Issues preventing progress, if any]

## Key Decisions
- **[Decision]**: [Brief rationale]

## Next Steps
1. [Ordered list of what should happen next]

## Critical Context
- [Any data, examples, or references needed to continue]
- [Or "(none)" if not applicable]

Keep each section concise. Preserve exact file paths, function names, and error messages."""

UPDATE_SUMMARIZATION_INSTRUCTIONS = """Update the existing structured summary with new information. RULES:
- PRESERVE all existing information from the previous summary
- ADD new progress, decisions, and context from the new messages
- UPDATE the Progress section: move items from "In Progress" to "Done" when completed
- UPDATE "Next Steps" based on what was accomplished
- PRESERVE exact file paths, function names, and error messages
- If something is no longer relevant, you may remove it

Use this EXACT format:

## Goal
[Preserve existing goals, add new ones if the task expanded]

## Constraints & Preferences
- [Preserve existing, add new ones discovered]

## Progress
### Done
- [x] [Include previously done items AND newly completed items]

### In Progress
- [ ] [Current work - update based on progress]

### Blocked
- [Current blockers - remove if resolved]

## Key Decisions
- **[Decision]**: [Brief rationale] (preserve all previous, add new)

## Next Steps
1. [Update based on current state]

## Critical Context
- [Preserve important context, add new if needed]

Keep each section concise. Preserve exact file paths, function names, and error messages."""

UPDATE_SUMMARIZATION_PROMPT = ("The messages above are NEW conversation messages to incorporate into the existing "
                               "summary provided in <previous-summary> tags.\n\n" + UPDATE_SUMMARIZATION_INSTRUCTIONS)


def truncate_for_summary(text: str, max_chars: int = TOOL_RESULT_MAX_CHARS) -> str:
    """``utils.js:truncateForSummary`` (JavaScript ``length`` = UTF-16 units)."""
    units = text.encode("utf-16-le", "surrogatepass")
    n = len(units) // 2
    if n <= max_chars:
        return text
    head = units[: 2 * max_chars].decode("utf-16-le", "surrogatepass")
    return f"{head}\n\n[... {n - max_chars} more characters truncated]"


def _js_json(v) -> str:
    """``JSON.stringify(v)``: compact separators, non-ASCII kept."""
    return json.dumps(v, ensure_ascii=False, separators=(",", ":"), default=str)


def serialize_conversation(messages: "list[Message]") -> tuple[str, int]:
    """``utils.js:serializeConversation``. Returns the text and the number of simulated reasoning tokens
    (``Message.pad_tokens``) serialised as ``[Assistant thinking]`` blocks, which are not materialised as text."""
    parts: list[str] = []
    thinking = 0
    for m in messages:
        if m.role == "user":
            if m.content:
                parts.append(f"[User]: {m.content}")
        elif m.role == "assistant":
            if m.pad_tokens:
                thinking += m.pad_tokens
                parts.append("[Assistant thinking]: ")
            if m.content:
                parts.append(f"[Assistant]: {m.content}")
            if m.tool_calls:
                calls = "; ".join(f"{c.name}(" + ", ".join(f"{k}={_js_json(v)}" for k, v in c.args.items()) + ")"
                                  for c in m.tool_calls)
                parts.append(f"[Assistant tool calls]: {calls}")
        elif m.role == "tool":
            if m.content:
                parts.append(f"[Tool result]: {truncate_for_summary(m.content)}")
    return "\n\n".join(parts), thinking


def summarization_prompt(archived: "list[Message]", custom_instructions: str = "") -> tuple[str, int]:
    """The single user message of Pi's summarisation request (``generateSummaryWithUsage``) and its unmaterialised
    reasoning tokens. A leading compaction-summary message is Pi's ``previousSummary``."""
    previous = None
    msgs = list(archived)
    if msgs and msgs[0].details.get("compaction"):
        c = msgs[0].content
        previous = c[len(SUMMARY_PREFIX):] if c.startswith(SUMMARY_PREFIX) else c
        msgs = msgs[1:]
    base = UPDATE_SUMMARIZATION_PROMPT if previous is not None else SUMMARIZATION_PROMPT
    if custom_instructions:
        base = f"{base}\n\nAdditional focus: {custom_instructions}"
    conv, thinking = serialize_conversation(msgs)
    text = f"<conversation>\n{conv}\n</conversation>\n\n"
    if previous is not None:
        text += f"<previous-summary>\n{previous}\n</previous-summary>\n\n"
    return text + base, thinking


def _tok(text: str) -> int:
    return int(math.ceil(len(text) / 4))


def summarization_request_tokens(archived: "list[Message]", custom_instructions: str = "",
                                 overhead: int = 4) -> tuple[int, int]:
    """(system tokens, user-message tokens) of the summarisation request, with the runtime's per-message overhead."""
    prompt, thinking = summarization_prompt(archived, custom_instructions)
    return _tok(SUMMARIZATION_SYSTEM_PROMPT) + overhead, _tok(prompt) + thinking + overhead
