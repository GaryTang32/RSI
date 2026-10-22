"""Negative controls for the SoL-Pi protocol: environment-specific tricks, do-less shortcuts, duds, family M."""
from __future__ import annotations

import re

from .runtime import AgentRuntime, Extension, ToolResult, ToolResultEvent, ToolSpec


class TailTrim(Extension):
    """Trick: silently keep only the last ``lines`` lines of long bash outputs."""

    name = "tail_trim"

    def __init__(self, lines: int = 40, min_bytes: int = 10 * 1024) -> None:
        super().__init__()
        self.lines, self.min_bytes = lines, min_bytes

    def register(self, rt: AgentRuntime) -> None:
        rt.on("tool_result", self._on)

    def _keep(self, lines: list[str]) -> list[str]:
        return lines[-self.lines:]

    def _on(self, ev: ToolResultEvent, rt: AgentRuntime):
        if ev.call.name != "bash" or len(ev.result.content.encode()) <= self.min_bytes:
            return None
        self.stats["trimmed"] += 1
        return ToolResult("\n".join(self._keep(ev.result.content.splitlines())), ev.result.is_error,
                          dict(ev.result.details))


class HeadTrim(TailTrim):
    """Trick: the mirror image of :class:`TailTrim`."""

    name = "head_trim"

    def _keep(self, lines: list[str]) -> list[str]:
        return lines[: self.lines]


class PytestQuiet(Extension):
    """Trick: every ``pytest`` invocation gets ``-q --tb=line``."""

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
    """Do-less shortcut: tell the agent not to re-run checks after edits."""

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
    """Dud: trim a boilerplate sentence from the system prompt."""

    name = "prompt_slim"

    def register(self, rt: AgentRuntime) -> None:
        rt.system_prompt = re.sub(r"\s*\(Be thorough[^)]*\)", "", rt.system_prompt)


class CostAttribution(Extension):
    """M5 instrument: journal the tokens of each request by role / tool; behaviour-neutral."""

    name = "cost_attribution"
    ENTRY = "sol-pi-cost-attribution-v1"

    def register(self, rt: AgentRuntime) -> None:
        rt.on("before_provider_request", self._on_request)

    def _on_request(self, msgs, rt: AgentRuntime) -> None:
        by_tool: dict[str, int] = {}
        for m in msgs:
            key = f"tool:{m.tool_name}" if m.role == "tool" else m.role
            by_tool[key] = by_tool.get(key, 0) + m.tokens()
        self.stats["requests"] += 1
        rt.append_entry(self.ENTRY, {"request": self.stats["requests"], "tokens": by_tool})


class FailBeforePassAfter(Extension):
    """M12 directive: judge each fix by a check that fails before it and passes after it."""

    name = "fail_before_pass_after"
    DIRECTIVE = ("[directive:fail_before_pass_after] Judge every fix with a check that fails before the fix and "
                 "passes after it; report both outcomes.")

    def register(self, rt: AgentRuntime) -> None:
        rt.system_prompt = rt.system_prompt + "\n" + self.DIRECTIVE
