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
* **duds** - harmless ideas with ~no effect: :class:`PromptSlim`;
* **improvement & evaluation (family M)** - the paper's largest proposal family (46 of 152)
  is about measuring and judging the harness, not about sending fewer tokens:
  :class:`CostAttribution` (M5 "Attribute cost to actions, phases, and providers": a
  behaviour-neutral instrument that journals per-tool token attribution) and
  :class:`FailBeforePassAfter` (M12 "Judge patches with fail-before/pass-after tests": a
  directive to confirm each fix with a check that failed before and passes after). Neither
  saves tokens here, so the dual gate should find no efficiency gain (none of the paper's four
  survivors came from family M either).
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


class CostAttribution(Extension):
    """M5 instrument: journals, per provider request, the tokens attributable to each tool's results in the
    request (``rt.append_entry``; the context and the tools are untouched)."""

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
