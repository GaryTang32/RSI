"""MockAgent: a deterministic agent policy that acts only on what its context shows.

The policy is a caricature of a competent coding agent with *habits* (a
:class:`Profile`): diagnose -> (read) -> fix -> verify per subtask, or read ->
compute -> write -> check for report-style tasks. Crucially it reads evidence
(failure lines, compiler errors, log lines, checker verdicts) from the messages
the runtime actually sends it - after every extension's projection. So a
mechanism that hides evidence (a placeholder, a trimmed log, a receipt without the
needed line) forces a recall, a readback, a re-run or a guess, exactly the
behavioural channel through which harness mechanisms change cost and success.

Knowledge carried across turns lives where a real model keeps it: in its own
assistant messages (``NOTE:`` / ``DONE:`` lines, which survive compaction
summaries). Two profiles model two backends: ``A`` (Sol-like: verifies, fuses,
plans, recalls) and ``B`` (Opus-like: other habits - fewer fused calls, less
planning, earlier stopping). Randomness is seeded per (task, seed).
"""
from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass
from typing import Optional

from ...core.llm import LLM, LLMResponse, Usage
from ...solpi.runtime import AgentRuntime, Message, ToolCall, ToolSpec
from .skills import SKILLS, Skill, skill_for  # noqa: F401  (re-exported)

REMINDER = "Online context compaction finished"


@dataclass(frozen=True)
class Profile:
    name: str
    prices: str = "sim-a"
    p_read_first: float = 0.5      # read the file before editing
    p_verify: float = 0.95         # run the targeted check after a fix
    p_fuse: float = 0.88           # use then_run when the tool offers it
    p_plan: float = 0.95           # keep a plan with update_plan when offered
    p_recall: float = 0.85         # recall a packed observation instead of re-running
    p_readback: float = 0.8        # read back an EPR source_artifact when a receipt lacks the evidence
    p_smart: float = 0.6           # targeted fallback commands (grep/-q/head) when evidence is missing
    p_misfix: float = 0.06         # wrong fix / misread although the evidence was visible
    p_final_check: float = 0.9     # full check before declaring done
    p_note_all: float = 0.3        # note evidence for other subtasks too
    p_rerun_full: float = 0.8      # re-run the full diagnostic before each new subtask (fresh view)
    p_explore: float = 0.6         # skim a few files before starting
    reasoning: tuple[int, int] = (150, 400)
    max_attempts: int = 3


PROFILES = {
    "A": Profile("A"),
    "B": Profile("B", prices="sim-b", p_read_first=0.8, p_verify=0.7, p_fuse=0.4, p_plan=0.35, p_recall=0.6,
                 p_readback=0.5, p_smart=0.45, p_misfix=0.09, p_final_check=0.5, p_note_all=0.2, p_rerun_full=0.6,
                 p_explore=0.3, reasoning=(80, 250)),
}


# ------------------------------------------------------------------ the policy
class MockAgent:
    """One episode of the deterministic agent (create one per task run)."""

    def __init__(self, profile: Profile, seed: int = 0, name: str = "mock-agent") -> None:
        self.p = profile
        self.name = name
        self.rng = random.Random(f"mockagent|{profile.name}|{seed}")
        self.started = False
        self.items: list[dict] = []
        self.skill: Optional[Skill] = None
        self.phase = "start"
        self.j = 0
        self.attempts: dict[str, int] = {}
        self.diag_runs: dict[str, int] = {}
        self.done: set[str] = set()
        self.failed: set[str] = set()
        self.calls: dict[str, dict] = {}          # call id -> {name, args}
        self.pending_ev: Optional[dict] = None
        self.values: dict[str, str] = {}
        self.todo: set[str] = set()
        self.use_plan = False
        self.plan_sent: dict[str, str] = {}
        self.final_checked = False
        self.recall_state: Optional[dict] = None
        self.last_read_file: Optional[str] = None
        self.begun: set[str] = set()
        self.focus: Optional[str] = None

    # ---- helpers
    def _tc(self, rt: AgentRuntime, name: str, args: dict) -> ToolCall:
        c = ToolCall(rt.next_call_id(), name, args)
        self.calls[c.id] = {"name": name, "args": args}
        return c

    def _msg(self, text: str, calls: list[ToolCall]) -> Message:
        return Message("assistant", text, tool_calls=tuple(calls), pad_tokens=self.rng.randint(*self.p.reasoning))

    @staticmethod
    def _tool_texts(msgs: list[Message]) -> list[Message]:
        return [m for m in msgs if m.role in ("tool", "assistant", "user")]

    def _search(self, msgs: list[Message], fn) -> Optional[dict]:
        for m in reversed(msgs):
            if m.role == "system":
                continue
            r = fn(m.content)
            if r:
                return r
        return None

    def _fresh_text(self, last: list[Message]) -> str:
        """Output of diagnostics/checks just run (plain bash or a fused then_run)."""
        out = []
        for m in last:
            c = self.calls.get(m.tool_call_id, {})
            if c.get("name") == "bash" or (c.get("name") in ("edit", "write") and "then_run" in c.get("args", {})):
                out.append(m.content)
        return "\n".join(out)

    def _last_results(self, msgs: list[Message]) -> list[Message]:
        out = []
        for m in reversed(msgs):
            if m.role == "assistant":
                break
            if m.role == "tool":
                out.append(m)
        return list(reversed(out))

    def _plan_call(self, rt, completed: Optional[str] = None, progress: Optional[dict] = None) -> Optional[ToolCall]:
        if not self.use_plan or "update_plan" not in rt.tools:
            return None
        steps = []
        cur = None
        for st in self.items:
            status = "completed" if st["id"] in self.done or st["id"] in self.failed else "pending"
            if status == "pending" and cur is None:
                status, cur = "in_progress", st["id"]
            steps.append({"id": st["id"], "goal": f"fix {st.get('key', st['id'])}", "status": status})
        final_status = "completed" if self.phase == "end" else ("in_progress" if cur is None else "pending")
        steps.append({"id": "final", "goal": "full check", "status": final_status})
        args = {"steps": steps}
        if progress:
            args["progress"] = progress
        return self._tc(rt, "update_plan", args)

    # ---- entry point
    def act(self, msgs: list[Message], tools: list[ToolSpec], rt: AgentRuntime) -> Message:
        names = {t.name: t for t in tools}
        self.fuse_ok = "edit" in names and "then_run" in names["edit"].parameters
        self.no_verify = "[directive:no_verify]" in msgs[0].content
        pre: list[ToolCall] = []
        if not self.started:
            self.started = True
            task = next(m.content for m in msgs if m.role == "user")
            self.skill = skill_for(task)
            self.items = self.skill.parse(task)
            self.todo = {st["id"] for st in self.items}
            self.use_plan = "update_plan" in names and self.rng.random() < self.p.p_plan
            pc = self._plan_call(rt)
            if pc:
                pre.append(pc)
        elif self.use_plan and any(m.role == "user" and REMINDER in m.content for m in self._last_user(msgs)):
            pc = self._plan_call(rt)
            if pc:
                pre.append(pc)
        if self.skill.style == "batch":
            text, calls = self._batch(msgs, rt)
        else:
            text, calls = self._per_subtask(msgs, rt)
        return self._msg(text, pre + calls)

    def _last_user(self, msgs):
        out = []
        for m in reversed(msgs):
            if m.role == "assistant":
                break
            if m.role == "user":
                out.append(m)
        return out

    # ---- per-subtask style (repofix, buildfix)
    def _cur(self) -> Optional[dict]:
        if self.focus is not None:
            f = next((st for st in self.items if st["id"] == self.focus), None)
            if f is not None and f["id"] not in self.done and f["id"] not in self.failed:
                return f
            self.focus = None
        for st in self.items:
            if st["id"] not in self.done and st["id"] not in self.failed:
                return st
        return None

    def _complete(self, st, rt, calls, ok: bool, text: list[str]) -> None:
        (self.done if ok else self.failed).add(st["id"])
        text.append(("DONE: " if ok else "DONE (gave up): ") + st.get("goal", st["id"]))
        pc = self._plan_call(rt, completed=st["id"], progress={"files_changed": [self.skill.file(st)] if hasattr(
            self.skill, "file") else [], "verification": ["check passed" if ok else "not verified"],
            "decisions": []}) if ok else None
        if pc:
            calls.append(pc)

    def _per_subtask(self, msgs, rt):
        sk = self.skill
        text: list[str] = []
        calls: list[ToolCall] = []
        last = self._last_results(msgs)
        last_text = "\n".join(m.content for m in last)
        # 1. interpret the result of the previous action
        if self.phase == "verify_wait":
            st = self._cur()
            v = sk.verdict(st, last_text) if st else None
            if v is True:
                self._complete(st, rt, calls, True, text)
                self.phase = "diagnose"
            elif v is False or v is None:
                self.attempts[st["id"]] = self.attempts.get(st["id"], 0) + 1
                if self.attempts[st["id"]] >= self.p.max_attempts:
                    self._complete(st, rt, calls, False, text)
                self.phase = "diagnose"
        elif self.phase == "final_wait":
            bad = [st for st in self.items if (st["id"] in self.done or st["id"] in self.failed) and
                   sk.find(st, last_text) and not ("NOTE:" in last_text)]
            if bad and last and last[0].is_error:
                for st in bad:
                    self.done.discard(st["id"])
                    self.failed.discard(st["id"])
                    self.attempts[st["id"]] = self.attempts.get(st["id"], 0) + 1
                    if self.attempts[st["id"]] > self.p.max_attempts:
                        self.failed.add(st["id"])
                self.phase = "diagnose"
            else:
                self.phase = "end"
        # 2. choose the next action
        if self.phase == "start":
            if self.rng.random() < self.p.p_explore and not getattr(self, "explored", False):
                self.explored = True
                files = sorted({sk.file(st) for st in self.items})
                self.rng.shuffle(files)
                calls += [self._tc(rt, "bash", {"command": "ls src tests"}), self._tc(rt, "read", {"path": "docs/GUIDE.md"})]
                calls += [self._tc(rt, "read", {"path": f}) for f in files[:2]]
                return "Exploring the repository first.", calls
            self.phase = "diag_wait"
            first = self.items[0] if self.items else None
            if first:
                self.diag_runs[first["id"]] = 1
            calls.append(self._tc(rt, "bash", {"command": sk.full_cmd()}))
            text.append(f"PLAN: {len(self.items)} subtasks; start by running the checks.")
            return "\n".join(text), calls
        if self.phase in ("diagnose", "diag_wait", "read_wait", "recall_wait"):
            st = self._cur()
            if st is None:
                if not self.final_checked and not self.no_verify and self.rng.random() < self.p.p_final_check:
                    self.final_checked = True
                    self.phase = "final_wait"
                    calls.append(self._tc(rt, "bash", {"command": sk.full_cmd()}))
                    text.append("All subtasks addressed; running the full check.")
                    return "\n".join(text), calls
                self.phase = "end"
            else:
                # a fresh output that shows another subtask failing: reopen it / switch focus to it
                ftext = self._fresh_text(last)
                if ftext and sk.find(st, ftext) is None:
                    other = next((o for o in self.items if o is not st and sk.find(o, ftext) is not None), None)
                    if other is not None:
                        self.done.discard(other["id"])
                        if other["id"] in self.failed:
                            self.failed.discard(other["id"])
                            self.diag_runs[other["id"]] = 0
                        self.focus = other["id"]
                        self.begun.add(other["id"])
                        st = other
                if st["id"] not in self.begun:
                    self.begun.add(st["id"])
                    fresh = any(self.calls.get(m.tool_call_id, {}).get("args", {}).get("command") == sk.full_cmd()
                                for m in last)
                    if not fresh and st is not self.items[0] and self.rng.random() < self.p.p_rerun_full:
                        self.phase = "diag_wait"
                        self.diag_runs[st["id"]] = self.diag_runs.get(st["id"], 0) + 1
                        calls.append(self._tc(rt, "bash", {"command": sk.full_cmd()}))
                        return "\n".join(text + ["Re-running the checks to see what still fails."]), calls
                ev = self._search(msgs, lambda t: sk.find(st, t))
                if ev is not None:
                    notes = [sk.note(st, ev)]
                    if self.rng.random() < self.p.p_note_all:
                        for other in self.items:
                            if other is st or other["id"] in self.done:
                                continue
                            e2 = sk.find(other, last_text)
                            if e2:
                                notes.append(sk.note(other, e2))
                    text += notes
                    if self.phase != "read_wait" and self.last_read_file != sk.file(st) and \
                            self.rng.random() < self.p.p_read_first:
                        self.phase = "read_wait"
                        self.last_read_file = sk.file(st)
                        calls.append(self._tc(rt, "read", {"path": sk.file(st)}))
                        return "\n".join(text), calls
                    wrong = self.rng.random() < self.p.p_misfix
                    name, args = sk.fix(st, ev, wrong)
                    will_verify = (not self.no_verify) and self.rng.random() < self.p.p_verify
                    if will_verify and self.fuse_ok and self.rng.random() < self.p.p_fuse:
                        args = {**args, "then_run": {"command": sk.verify_cmd(st)}}
                        self.phase = "verify_wait"
                    elif will_verify:
                        self.phase = "verify"
                    else:
                        self.phase = "fixed_unverified"
                    calls.append(self._tc(rt, name, args))
                    text.append(f"Fixing {sk.file(st)}.")
                    return "\n".join(text), calls
                # evidence not visible: recall / readback / rerun / give up
                call = self._fallback(st, msgs, rt)
                if call is not None:
                    calls.append(call)
                    return "\n".join(text + ["Evidence not visible; retrieving it."]), calls
                self._complete(st, rt, calls, False, text)
                return self._per_subtask_next(msgs, rt, text, calls)
        if self.phase == "verify":
            st = self._cur()
            self.phase = "verify_wait"
            calls.append(self._tc(rt, "bash", {"command": sk.verify_cmd(st)}))
            return "\n".join(text + ["Verifying."]), calls
        if self.phase == "fixed_unverified":
            st = self._cur()
            self._complete(st, rt, calls, True, text)
            self.phase = "diagnose"
            return self._per_subtask_next(msgs, rt, text, calls)
        if self.phase == "end":
            return "\n".join(text + ["All done."]), []
        return self._per_subtask_next(msgs, rt, text, calls)

    def _per_subtask_next(self, msgs, rt, text, calls):
        self.phase = "diagnose"
        st = self._cur()
        if st is None:
            if not self.final_checked and not self.no_verify and self.rng.random() < self.p.p_final_check:
                self.final_checked = True
                self.phase = "final_wait"
                calls.append(self._tc(rt, "bash", {"command": self.skill.full_cmd()}))
                return "\n".join(text + ["Running the full check."]), calls
            self.phase = "end"
            return "\n".join(text + ["All done."]), []
        ev = self._search(msgs, lambda t: self.skill.find(st, t))
        if ev is not None and self.rng.random() < self.p.p_rerun_full:
            ev = None       # habit: get a fresh view of what still fails
        if ev is not None:
            # act on already-visible evidence next turn (keeps one mutation per message)
            self.phase = "diagnose"
            calls.append(self._tc(rt, "read", {"path": self.skill.file(st)}))
            self.last_read_file = self.skill.file(st)
            self.phase = "read_wait"
            return "\n".join(text), calls
        self.phase = "diag_wait"
        self.diag_runs[st["id"]] = self.diag_runs.get(st["id"], 0) + 1
        calls.append(self._tc(rt, "bash", {"command": self.skill.full_cmd()}))
        return "\n".join(text), calls

    def _fallback(self, st, msgs, rt) -> Optional[ToolCall]:
        sk = self.skill
        # packed observation of a diagnostic output?
        ph = self._search(msgs, lambda t: {"id": re.search(r"^id: (obs_[a-f0-9]{24})$", t, re.M).group(1)}
                          if t.startswith("[large tool result replaced") else None)
        if ph and "obs_recall" in rt.tools and self.rng.random() < self.p.p_recall:
            rs = self.recall_state if self.recall_state and self.recall_state["id"] == ph["id"] else \
                {"id": ph["id"], "offset": 0, "eof": False}
            if not rs["eof"]:
                last = self._last_results(msgs)
                for m in last:
                    mm = re.match(r"\[obs_recall id=(\S+) offset=\d+ next_offset=(\d+) eof=(\w+)\]", m.content)
                    if mm and mm.group(1) == rs["id"]:
                        rs = {"id": rs["id"], "offset": int(mm.group(2)), "eof": mm.group(3) == "True"}
                self.recall_state = rs
                if not rs["eof"]:
                    return self._tc(rt, "obs_recall", {"id": rs["id"], "offset": rs["offset"]})
        rc = self._search(msgs, lambda t: {"path": re.search(r"^source_artifact=(\S+)$", t, re.M).group(1)}
                          if t.startswith("sol_pi_evidence_receipt_v1") else None)
        if rc and self.rng.random() < self.p.p_readback and not self.attempts.get(("rb", rc["path"])):
            self.attempts[("rb", rc["path"])] = 1
            return self._tc(rt, "bash", {"command": f"grep -n -m 20 {sk.pattern(st)} {rc['path']}"})
        n = self.diag_runs.get(st["id"], 0)
        if n < self.p.max_attempts:
            self.diag_runs[st["id"]] = n + 1
            smart = self.rng.random() < self.p.p_smart
            return self._tc(rt, "bash", {"command": sk.diag(st, n == 0, smart)})
        return None

    # ---- batch style (logtriage, datalookup)
    def _data_view(self, msgs) -> Optional[str]:
        """The latest full view of the data file (None if absent or replaced by a placeholder)."""
        for m in reversed(msgs):
            if m.role == "tool" and self.calls.get(m.tool_call_id, {}).get("args", {}).get("command") == \
                    self.skill.read_cmd:
                return None if m.content.startswith("[large tool result replaced") else m.content
        return None

    def _batch(self, msgs, rt):
        sk = self.skill
        text: list[str] = []
        calls: list[ToolCall] = []
        last = self._last_results(msgs)
        if self.phase == "start":
            self.phase = "compute"
            if self.rng.random() < self.p.p_explore:
                calls.append(self._tc(rt, "read", {"path": "docs/GUIDE.md"}))
            calls.append(self._tc(rt, "bash", {"command": sk.read_cmd}))
            return "PLAN: read the data, compute every item, write the file, run the check.", calls
        if self.phase == "smart_wait":
            for m in last:
                c = self.calls.get(m.tool_call_id, {})
                st = next((s for s in self.items if c.get("args", {}).get("command") == sk.smart(s)), None)
                if st is not None:
                    v = sk.parse_smart(st, m.content)
                    if v is not None:
                        self.values[st["id"]] = v
                        self.todo.discard(st["id"])
            self.phase = "compute"
        if self.phase == "check_wait":
            passed, bad = sk.check(self.items, "\n".join(m.content for m in last))
            if passed:
                self.phase = "end"
                return "DONE: all items verified. All done.", []
            self.attempts["check"] = self.attempts.get("check", 0) + 1
            if self.attempts["check"] >= self.p.max_attempts:
                self.phase = "end"
                return "DONE (gave up): checks still failing.", []
            self.todo = set(bad) if bad else {st["id"] for st in self.items}
            self.done -= self.todo
            self.phase = "recompute"
        if self.phase == "recompute":
            u = self.rng.random()
            if u < self.p.p_smart:
                self.phase = "smart_wait"
                for st in self.items:
                    if st["id"] in self.todo:
                        calls.append(self._tc(rt, "bash", {"command": sk.smart(st)}))
                return "Re-deriving the failed items with targeted commands.", calls
            data = self._data_view(msgs)
            if data is not None and sk.is_data(data):
                # the data is still in context: re-derive the failed items from it directly
                for st in self.items:
                    if st["id"] in self.todo:
                        v = sk.compute(st, data)
                        if v is not None:
                            self.values[st["id"]] = v
                            self.todo.discard(st["id"])
                self.phase = "compute"
                return self._batch(msgs, rt)
            if u > self.p.p_smart + (1 - self.p.p_smart) * self.p.p_recall:
                # replaced by a placeholder and not re-fetched: resubmit from memory (the earlier values)
                self.todo = set()
                self.phase = "compute"
                return self._batch(msgs, rt)
            self.phase = "compute"
            calls.append(self._tc(rt, "bash", {"command": sk.read_cmd}))
            return "Re-reading the data.", calls
        if self.phase == "compute":
            data = self._data_view(msgs)
            if data is not None and sk.is_data(data):
                for st in self.items:
                    if st["id"] in self.todo:
                        v = sk.compute(st, data)
                        if v is not None:
                            if self.rng.random() < self.p.p_misfix:
                                v = sk.perturb(st, v, self.rng)
                            self.values[st["id"]] = v
                            self.todo.discard(st["id"])
            missing = [st for st in self.items if st["id"] not in self.values]
            if missing and self.rng.random() < self.p.p_smart and self.attempts.get("smart", 0) < 2:
                self.attempts["smart"] = self.attempts.get("smart", 0) + 1
                self.phase = "smart_wait"
                return "Some items are not visible; querying them directly.", \
                    [self._tc(rt, "bash", {"command": sk.smart(st)}) for st in missing]
            for st in missing:
                self.values[st["id"]] = "unknown"
            self.todo = set()
            content = sk.render(self.items, self.values)
            text.append("NOTE: computed " + ", ".join(f"{st['id']}={self.values.get(st['id'])}" for st in self.items))
            will_check = not self.no_verify
            args = {"path": sk.out_file, "content": content}
            if will_check and self.fuse_ok and self.rng.random() < self.p.p_fuse:
                args["then_run"] = {"command": sk.check_cmd}
                self.phase = "check_wait"
            elif will_check:
                self.phase = "check"
            else:
                self.phase = "end_unverified"
            newly = {st["id"] for st in self.items} - self.done
            self.done |= newly
            pc = self._plan_call(rt, progress={"files_changed": [sk.out_file], "verification": [],
                                               "decisions": [f"computed {len(newly)} items"]}) if newly else None
            calls.append(self._tc(rt, "write", args))
            if pc:
                calls.append(pc)
            return "\n".join(text), calls
        if self.phase == "check":
            self.phase = "check_wait"
            return "Validating.", [self._tc(rt, "bash", {"command": sk.check_cmd})]
        if self.phase == "end_unverified":
            self.phase = "end"
        return "All done.", []


class MockAgentLLM(LLM):
    """Carrier for a :class:`Profile` through :class:`rsi.core.Evaluator` (``llm=``):
    ``new_agent(seed)`` creates a fresh :class:`MockAgent` per episode."""

    def __init__(self, profile: str | Profile = "A") -> None:
        super().__init__()
        self.profile = PROFILES[profile] if isinstance(profile, str) else profile
        self.name = f"mock-agent-{self.profile.name}"
        self.prices = self.profile.prices

    def new_agent(self, seed: int) -> MockAgent:
        return MockAgent(self.profile, seed)

    def _complete(self, prompt, *, system, max_tokens, seed) -> LLMResponse:
        return LLMResponse("", Usage(), self.name, error="MockAgentLLM is an agent backend, not a text model")


# ------------------------------------------------------------------ LLM backend adapter
ACTION_PROTOCOL = """
Reply with ONE JSON object and nothing else:
{"thought": "<one or two sentences>", "tool_calls": [{"name": "<tool>", "args": {...}}]}
Use an empty tool_calls list only when the task is complete (then put your summary in "thought")."""


class LLMAgent:
    """Adapter that lets a text LLM (e.g. ``claude -p``) act as the agent backend."""

    def __init__(self, llm: LLM, max_chars_per_message: int = 60000) -> None:
        self.llm = llm
        self.name = getattr(llm, "name", "llm")
        self.max_chars = max_chars_per_message

    def act(self, msgs: list[Message], tools: list[ToolSpec], rt: AgentRuntime) -> Message:
        parts = []
        for m in msgs[1:]:
            if m.role == "assistant":
                tc = json.dumps([{"name": c.name, "args": c.args} for c in m.tool_calls])
                parts.append(f"ASSISTANT: {m.content}\nTOOL_CALLS: {tc}")
            elif m.role == "tool":
                parts.append(f"TOOL RESULT ({m.tool_name}{', error' if m.is_error else ''}):\n"
                             f"{m.content[: self.max_chars]}")
            else:
                parts.append(f"USER: {m.content}")
        resp = self.llm.complete("\n\n".join(parts), system=msgs[0].content + ACTION_PROTOCOL, role="agent",
                                 seed=rt.turns)
        if not resp.ok:     # a backend outage must not look like an agent that chose to stop
            raise RuntimeError(f"infra: agent backend error: {resp.error}")
        try:
            from ...core.llm import extract_json
            d = extract_json(resp.text)
        except ValueError:
            d = {"thought": resp.text[:500], "tool_calls": []}
        calls = tuple(ToolCall(rt.next_call_id(), str(c.get("name")), dict(c.get("args") or {}))
                      for c in (d.get("tool_calls") or []) if isinstance(c, dict))
        return Message("assistant", str(d.get("thought", ""))[:2000], tool_calls=calls)
