"""Pi 0.85.1 native-compaction request (prompts verbatim, MIT): its text and size; the split-turn path is not ported."""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

from .meter import estimate_tokens

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
    """``utils.js:truncateForSummary`` (lengths in UTF-16 units)."""
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
    """``utils.js:serializeConversation``: the text and the unmaterialised ``[Assistant thinking]`` tokens."""
    parts: list[str] = []
    thinking = 0
    for m in messages:
        if m.role == "user" and m.content:
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
        elif m.role == "tool" and m.content:
            parts.append(f"[Tool result]: {truncate_for_summary(m.content)}")
    return "\n\n".join(parts), thinking


def summarization_prompt(archived: "list[Message]", custom_instructions: str = "") -> tuple[str, int]:
    """The user message of ``generateSummaryWithUsage``; a leading compaction summary is ``previousSummary``."""
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


def summarization_request_tokens(archived: "list[Message]", custom_instructions: str = "",
                                 overhead: int = 4) -> tuple[int, int]:
    """(system tokens, user-message tokens) of the summarisation request, with the runtime's per-message overhead."""
    prompt, thinking = summarization_prompt(archived, custom_instructions)
    return estimate_tokens(SUMMARIZATION_SYSTEM_PROMPT) + overhead, estimate_tokens(prompt) + thinking + overhead
