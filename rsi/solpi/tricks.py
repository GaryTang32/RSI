"""Negative controls for the SoL-Pi protocol: environment-specific tricks and do-less shortcuts.

SoL-Pi's thesis is that an efficiency objective under a capability floor, judged
across varied environments with an isolated held-out set, finds reusable
mechanisms rather than benchmark hacks. To test that (S5/S6) the candidate pool
must also contain things a naive optimiser would happily keep:

* **environment-specific tricks** - save tokens by assuming something about one
  environment's output format:
  :class:`TailTrim` (silently keep only the last N lines of long outputs - fine
  where the verdict is at the tail, destructive where the first error is at the
  top or the whole output matters), :class:`HeadTrim` (the mirror image),
  :class:`PytestQuiet` (hard-coded rewrite of ``pytest`` to ``pytest -q --tb=line``);
* **do-less shortcuts** - save tokens by doing less work:
  :class:`NoVerify` (a harness directive to skip re-running checks),
  :class:`TurnCap` (a lower turn limit);
* **duds** - harmless ideas with ~no effect: :class:`PromptSlim`.
"""
from __future__ import annotations

import re

from .runtime import AgentRuntime, Extension, ToolResult, ToolResultEvent, ToolSpec


class TailTrim(Extension):
    name = "tail_trim"

    def __init__(self, lines: int = 40, min_bytes: int = 10 * 1024) -> None:
        super().__init__()
        self.lines, self.min_bytes = lines, min_bytes

    def register(self, rt: AgentRuntime) -> None:
        rt.on("tool_result", self._on)

    def _on(self, ev: ToolResultEvent, rt: AgentRuntime):
        if ev.call.name != "bash" or len(ev.result.content.encode()) <= self.min_bytes:
            return None
        self.stats["trimmed"] += 1
        return ToolResult("\n".join(ev.result.content.splitlines()[-self.lines:]), ev.result.is_error,
                          dict(ev.result.details))


class HeadTrim(TailTrim):
    name = "head_trim"

    def _on(self, ev: ToolResultEvent, rt: AgentRuntime):
        if ev.call.name != "bash" or len(ev.result.content.encode()) <= self.min_bytes:
            return None
        self.stats["trimmed"] += 1
        return ToolResult("\n".join(ev.result.content.splitlines()[: self.lines]), ev.result.is_error,
                          dict(ev.result.details))


class PytestQuiet(Extension):
    """Hard-coded command rewrite: every ``pytest`` invocation gets ``-q --tb=line``."""

    name = "pytest_quiet"

    def register(self, rt: AgentRuntime) -> None:
        base = rt.tools["bash"]

        def execute(args, r, cid):
            cmd = str(args.get("command", ""))
            if re.search(r"(?:^|\s)pytest(?:\s|$)", cmd) and "-q" not in cmd:
                cmd = re.sub(r"(?:^|\s)pytest(?=\s|$)", lambda m: m.group(0) + " -q --tb=line", cmd, count=1)
                self.stats["rewritten"] += 1
            return base.execute({**args, "command": cmd}, r, cid)

        rt.register_tool(ToolSpec("bash", base.description, base.parameters, execute), replaces="bash")


class NoVerify(Extension):
    """Do-less shortcut: tells the agent not to re-run checks after edits."""

    name = "no_verify"
    DIRECTIVE = "[directive:no_verify] Efficiency rule: do not re-run tests or checks after edits; finish as soon as " \
                "the fixes are written."

    def register(self, rt: AgentRuntime) -> None:
        rt.system_prompt = rt.system_prompt + "\n" + self.DIRECTIVE


class TurnCap(Extension):
    """Do-less shortcut: a lower turn limit."""

    name = "turn_cap"

    def __init__(self, max_turns: int = 24) -> None:
        super().__init__()
        self.max_turns = max_turns

    def register(self, rt: AgentRuntime) -> None:
        rt.max_turns = min(rt.max_turns, self.max_turns)


class PromptSlim(Extension):
    """Dud: trims boilerplate sentences from the system prompt (tiny effect)."""

    name = "prompt_slim"

    def register(self, rt: AgentRuntime) -> None:
        rt.system_prompt = re.sub(r"\s*\(Be thorough[^)]*\)", "", rt.system_prompt)
