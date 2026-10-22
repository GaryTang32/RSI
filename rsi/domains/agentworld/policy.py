"""Deterministic agent backends that act only on what their (projected) context shows, plus an LLM adapter."""
from __future__ import annotations

import json
import random
import re
from collections import Counter
from dataclasses import dataclass
from itertools import takewhile
from typing import Optional

from ...core.llm import LLM, LLMResponse, Usage, extract_json
from ...solpi.runtime import AgentRuntime, Message, ToolCall, ToolSpec
from .skills import Skill, skill_for

REMINDER = "Online context compaction finished"
PLACEHOLDER = "[large tool result replaced"


@dataclass(frozen=True)
class Profile:
    """Habits of one simulated backend (probabilities per decision)."""

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


class MockAgent:
    """One episode of the deterministic agent; knowledge across turns lives in its own NOTE/DONE lines."""

    def __init__(self, profile: Profile, seed: int = 0, name: str = "mock-agent") -> None:
        self.p = profile
        self.name = name
        self.rng = random.Random(f"mockagent|{profile.name}|{seed}")
        self.started = self.explored = self.fuse_ok = self.no_verify = self.use_plan = self.final_checked = False
        self.skill: Optional[Skill] = None
        self.phase = "start"
        self.items: list[dict] = []
        self.calls: dict[str, dict] = {}          # call id -> {name, args}
        self.values: dict[str, str] = {}
        self.attempts: Counter = Counter()
        self.diag_runs: Counter = Counter()
        self.done: set[str] = set()
        self.failed: set[str] = set()
        self.todo: set[str] = set()
        self.begun: set[str] = set()
        self.recall_state: Optional[dict] = None
        self.last_read_file: Optional[str] = None
        self.focus: Optional[str] = None

    def _tc(self, rt: AgentRuntime, name: str, args: dict) -> ToolCall:
        c = ToolCall(rt.next_call_id(), name, args)
        self.calls[c.id] = {"name": name, "args": args}
        return c

    def _command(self, m: Message) -> Optional[str]:
        return self.calls.get(m.tool_call_id, {}).get("args", {}).get("command")

    def _msg(self, text: str, calls: list[ToolCall]) -> Message:
        return Message("assistant", text, tool_calls=tuple(calls), pad_tokens=self.rng.randint(*self.p.reasoning))

    @staticmethod
    def _search(msgs: list[Message], fn) -> Optional[dict]:
        """``fn`` of the newest non-system message for which it is truthy."""
        for m in reversed(msgs):
            if m.role != "system" and (r := fn(m.content)):
                return r
        return None

    @staticmethod
    def _since_reply(msgs: list[Message], role: str) -> list[Message]:
        """Messages of ``role`` after the last assistant reply, oldest first."""
        return [m for m in takewhile(lambda m: m.role != "assistant", reversed(msgs)) if m.role == role][::-1]

    def _fresh_text(self, last: list[Message]) -> str:
        """Output of diagnostics/checks just run (plain bash or a fused then_run)."""
        def ran_command(c: dict) -> bool:
            return c.get("name") == "bash" or (c.get("name") in ("edit", "write") and "then_run" in c.get("args", {}))
        return "\n".join(m.content for m in last if ran_command(self.calls.get(m.tool_call_id, {})))

    def _plan_call(self, rt, progress: Optional[dict] = None) -> Optional[ToolCall]:
        if not self.use_plan or "update_plan" not in rt.tools:
            return None
        steps, cur = [], None
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

    def act(self, msgs: list[Message], tools: list[ToolSpec], rt: AgentRuntime) -> Message:
        names = {t.name: t for t in tools}
        self.fuse_ok = "edit" in names and "then_run" in names["edit"].parameters
        self.no_verify = "[directive:no_verify]" in msgs[0].content
        pc = None
        if not self.started:
            self.started = True
            task = next(m.content for m in msgs if m.role == "user")
            self.skill = skill_for(task)
            self.items = self.skill.parse(task)
            self.todo = {st["id"] for st in self.items}
            self.use_plan = "update_plan" in names and self.rng.random() < self.p.p_plan
            pc = self._plan_call(rt)
        elif self.use_plan and any(REMINDER in m.content for m in self._since_reply(msgs, "user")):
            pc = self._plan_call(rt)
        text, calls = (self._batch if self.skill.style == "batch" else self._per_subtask)(msgs, rt)
        return self._msg(text, ([pc] if pc else []) + calls)

    def _open(self, st: dict) -> bool:
        return st["id"] not in self.done and st["id"] not in self.failed

    def _cur(self) -> Optional[dict]:
        if self.focus is not None:
            f = next((st for st in self.items if st["id"] == self.focus), None)
            if f is not None and self._open(f):
                return f
            self.focus = None
        return next((st for st in self.items if self._open(st)), None)

    def _complete(self, st, rt, calls, ok: bool, text: list[str]) -> None:
        (self.done if ok else self.failed).add(st["id"])
        text.append(("DONE: " if ok else "DONE (gave up): ") + st.get("goal", st["id"]))
        if ok:
            pc = self._plan_call(rt, progress={"files_changed": [self.skill.file(st)], "verification": ["check passed"],
                                               "decisions": []})
            if pc:
                calls.append(pc)

    def _final_check(self, rt, text: list[str], calls: list, note: str):
        if self.final_checked or self.no_verify or self.rng.random() >= self.p.p_final_check:
            return None
        self.final_checked = True
        self.phase = "final_wait"
        calls.append(self._tc(rt, "bash", {"command": self.skill.full_cmd()}))
        return "\n".join(text + [note]), calls

    def _finish(self, text: list[str]):
        self.phase = "end"
        return "\n".join(text + ["All done."]), []

    def _rediagnose(self, st, rt) -> ToolCall:
        self.phase = "diag_wait"
        self.diag_runs[st["id"]] += 1
        return self._tc(rt, "bash", {"command": self.skill.full_cmd()})

    def _per_subtask(self, msgs, rt):
        sk = self.skill
        text: list[str] = []
        calls: list[ToolCall] = []
        last = self._since_reply(msgs, "tool")
        last_text = "\n".join(m.content for m in last)
        # interpret the result of the previous action
        if self.phase == "verify_wait":
            st = self._cur()
            if (sk.verdict(st, last_text) if st else None) is True:
                self._complete(st, rt, calls, True, text)
            else:
                self.attempts[st["id"]] += 1
                if self.attempts[st["id"]] >= self.p.max_attempts:
                    self._complete(st, rt, calls, False, text)
            self.phase = "diagnose"
        elif self.phase == "final_wait":
            bad = [st for st in self.items
                   if not self._open(st) and sk.find(st, last_text) and "NOTE:" not in last_text]
            if bad and last and last[0].is_error:
                for st in bad:
                    self.done.discard(st["id"])
                    self.failed.discard(st["id"])
                    self.attempts[st["id"]] += 1
                    if self.attempts[st["id"]] > self.p.max_attempts:
                        self.failed.add(st["id"])
                self.phase = "diagnose"
            else:
                self.phase = "end"
        # choose the next action
        if self.phase == "start":
            if self.rng.random() < self.p.p_explore and not self.explored:
                self.explored = True
                files = sorted({sk.file(st) for st in self.items})
                self.rng.shuffle(files)
                calls += [self._tc(rt, "bash", {"command": "ls src tests"}),
                          self._tc(rt, "read", {"path": "docs/GUIDE.md"})]
                calls += [self._tc(rt, "read", {"path": f}) for f in files[:2]]
                return "Exploring the repository first.", calls
            self.phase = "diag_wait"
            if self.items:
                self.diag_runs[self.items[0]["id"]] = 1
            calls.append(self._tc(rt, "bash", {"command": sk.full_cmd()}))
            text.append(f"PLAN: {len(self.items)} subtasks; start by running the checks.")
            return "\n".join(text), calls
        if self.phase in ("diagnose", "diag_wait", "read_wait"):
            st = self._cur()
            if st is not None:
                return self._work_on(st, msgs, rt, last, last_text, text, calls)
            return self._final_check(rt, text, calls, "All subtasks addressed; running the full check.") or \
                self._finish(text)
        if self.phase == "verify":
            self.phase = "verify_wait"
            calls.append(self._tc(rt, "bash", {"command": sk.verify_cmd(self._cur())}))
            return "\n".join(text + ["Verifying."]), calls
        if self.phase == "fixed_unverified":
            self._complete(self._cur(), rt, calls, True, text)
            self.phase = "diagnose"
            return self._per_subtask_next(msgs, rt, text, calls)
        return self._finish(text)

    def _work_on(self, st, msgs, rt, last, last_text, text, calls):
        sk = self.skill
        # a fresh output showing another subtask failing: reopen it and switch focus to it
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
            fresh = any(self._command(m) == sk.full_cmd() for m in last)
            if not fresh and st is not self.items[0] and self.rng.random() < self.p.p_rerun_full:
                calls.append(self._rediagnose(st, rt))
                return "\n".join(text + ["Re-running the checks to see what still fails."]), calls
        ev = self._search(msgs, lambda t: sk.find(st, t))
        if ev is None:
            call = self._fallback(st, msgs, rt)
            if call is not None:
                calls.append(call)
                return "\n".join(text + ["Evidence not visible; retrieving it."]), calls
            self._complete(st, rt, calls, False, text)
            return self._per_subtask_next(msgs, rt, text, calls)
        text.append(sk.note(st, ev))
        if self.rng.random() < self.p.p_note_all:
            for other in self.items:
                if other is not st and other["id"] not in self.done and (e2 := sk.find(other, last_text)):
                    text.append(sk.note(other, e2))
        if self.phase != "read_wait" and self.last_read_file != sk.file(st) and \
                self.rng.random() < self.p.p_read_first:
            self.phase = "read_wait"
            self.last_read_file = sk.file(st)
            calls.append(self._tc(rt, "read", {"path": sk.file(st)}))
            return "\n".join(text), calls
        name, args = sk.fix(st, ev, self.rng.random() < self.p.p_misfix)
        will_verify = not self.no_verify and self.rng.random() < self.p.p_verify
        if will_verify and self.fuse_ok and self.rng.random() < self.p.p_fuse:
            args = {**args, "then_run": {"command": sk.verify_cmd(st)}}
            self.phase = "verify_wait"
        else:
            self.phase = "verify" if will_verify else "fixed_unverified"
        calls.append(self._tc(rt, name, args))
        text.append(f"Fixing {sk.file(st)}.")
        return "\n".join(text), calls

    def _per_subtask_next(self, msgs, rt, text, calls):
        st = self._cur()
        if st is None:
            return self._final_check(rt, text, calls, "Running the full check.") or self._finish(text)
        ev = self._search(msgs, lambda t: self.skill.find(st, t))
        if ev is not None and self.rng.random() >= self.p.p_rerun_full:
            # act on the visible evidence next turn (one mutation per message)
            calls.append(self._tc(rt, "read", {"path": self.skill.file(st)}))
            self.last_read_file = self.skill.file(st)
            self.phase = "read_wait"
            return "\n".join(text), calls
        calls.append(self._rediagnose(st, rt))
        return "\n".join(text), calls

    def _fallback(self, st, msgs, rt) -> Optional[ToolCall]:
        """Evidence not visible: recall a packed observation, read back a receipt's log, or re-run a diagnostic."""
        sk = self.skill
        ph = self._search(msgs, lambda t: {"id": re.search(r"^id: (obs_[a-f0-9]{24})$", t, re.M).group(1)}
                          if t.startswith(PLACEHOLDER) else None)
        if ph and "obs_recall" in rt.tools and self.rng.random() < self.p.p_recall:
            rs = self.recall_state if self.recall_state and self.recall_state["id"] == ph["id"] else \
                {"id": ph["id"], "offset": 0, "eof": False}
            if not rs["eof"]:
                for m in self._since_reply(msgs, "tool"):
                    mm = re.match(r"\[obs_recall id=(\S+) offset=\d+ next_offset=(\d+) eof=(\w+)\]", m.content)
                    if mm and mm.group(1) == rs["id"]:
                        rs = {"id": rs["id"], "offset": int(mm.group(2)), "eof": mm.group(3) == "True"}
                self.recall_state = rs
                if not rs["eof"]:
                    return self._tc(rt, "obs_recall", {"id": rs["id"], "offset": rs["offset"]})
        rc = self._search(msgs, lambda t: {"path": re.search(r"^source_artifact=(\S+)$", t, re.M).group(1)}
                          if t.startswith("sol_pi_evidence_receipt_v1") else None)
        if rc and self.rng.random() < self.p.p_readback and not self.attempts["rb", rc["path"]]:
            self.attempts["rb", rc["path"]] = 1
            return self._tc(rt, "bash", {"command": f"grep -n -m 20 {sk.pattern(st)} {rc['path']}"})
        n = self.diag_runs[st["id"]]
        if n < self.p.max_attempts:
            self.diag_runs[st["id"]] = n + 1
            return self._tc(rt, "bash", {"command": sk.diag(st, n == 0, self.rng.random() < self.p.p_smart)})
        return None

    def _data_view(self, msgs) -> Optional[str]:
        """The latest full view of the data file (None if absent or replaced by a placeholder)."""
        for m in reversed(msgs):
            if m.role == "tool" and self._command(m) == self.skill.read_cmd:
                return None if m.content.startswith(PLACEHOLDER) else m.content
        return None

    def _derive(self, data: str, misfix: bool) -> None:
        for st in self.items:
            if st["id"] in self.todo:
                v = self.skill.compute(st, data)
                if v is not None:
                    if misfix and self.rng.random() < self.p.p_misfix:
                        v = self.skill.perturb(st, v, self.rng)
                    self.values[st["id"]] = v
                    self.todo.discard(st["id"])

    def _batch(self, msgs, rt):
        sk = self.skill
        calls: list[ToolCall] = []
        last = self._since_reply(msgs, "tool")
        if self.phase == "start":
            self.phase = "compute"
            if self.rng.random() < self.p.p_explore:
                calls.append(self._tc(rt, "read", {"path": "docs/GUIDE.md"}))
            calls.append(self._tc(rt, "bash", {"command": sk.read_cmd}))
            return "PLAN: read the data, compute every item, write the file, run the check.", calls
        if self.phase == "smart_wait":
            for m in last:
                st = next((s for s in self.items if self._command(m) == sk.smart(s)), None)
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
            self.attempts["check"] += 1
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
                calls += [self._tc(rt, "bash", {"command": sk.smart(st)}) for st in self.items if st["id"] in self.todo]
                return "Re-deriving the failed items with targeted commands.", calls
            self.phase = "compute"
            data = self._data_view(msgs)
            if data is not None and sk.is_data(data):
                self._derive(data, misfix=False)          # the data is still in context
                return self._batch(msgs, rt)
            if u > self.p.p_smart + (1 - self.p.p_smart) * self.p.p_recall:
                self.todo = set()                         # resubmit the earlier values from memory
                return self._batch(msgs, rt)
            calls.append(self._tc(rt, "bash", {"command": sk.read_cmd}))
            return "Re-reading the data.", calls
        if self.phase == "compute":
            data = self._data_view(msgs)
            if data is not None and sk.is_data(data):
                self._derive(data, misfix=True)
            missing = [st for st in self.items if st["id"] not in self.values]
            if missing and self.rng.random() < self.p.p_smart and self.attempts["smart"] < 2:
                self.attempts["smart"] += 1
                self.phase = "smart_wait"
                return "Some items are not visible; querying them directly.", \
                    [self._tc(rt, "bash", {"command": sk.smart(st)}) for st in missing]
            for st in missing:
                self.values[st["id"]] = "unknown"
            self.todo = set()
            text = "NOTE: computed " + ", ".join(f"{st['id']}={self.values.get(st['id'])}" for st in self.items)
            args = {"path": sk.out_file, "content": sk.render(self.items, self.values)}
            if not self.no_verify and self.fuse_ok and self.rng.random() < self.p.p_fuse:
                args["then_run"] = {"command": sk.check_cmd}
                self.phase = "check_wait"
            else:
                self.phase = "end_unverified" if self.no_verify else "check"
            newly = {st["id"] for st in self.items} - self.done
            self.done |= newly
            pc = self._plan_call(rt, progress={"files_changed": [sk.out_file], "verification": [],
                                               "decisions": [f"computed {len(newly)} items"]}) if newly else None
            calls.append(self._tc(rt, "write", args))
            if pc:
                calls.append(pc)
            return text, calls
        if self.phase == "check":
            self.phase = "check_wait"
            return "Validating.", [self._tc(rt, "bash", {"command": sk.check_cmd})]
        if self.phase == "end_unverified":
            self.phase = "end"
        return "All done.", []


class MockAgentLLM(LLM):
    """Carries a :class:`Profile` through :class:`rsi.core.Evaluator`; ``new_agent(seed)`` starts an episode."""

    def __init__(self, profile: str | Profile = "A") -> None:
        super().__init__()
        self.profile = PROFILES[profile] if isinstance(profile, str) else profile
        self.name = f"mock-agent-{self.profile.name}"
        self.prices = self.profile.prices

    def new_agent(self, seed: int) -> MockAgent:
        return MockAgent(self.profile, seed)

    def _complete(self, prompt, *, system, max_tokens, seed) -> LLMResponse:
        return LLMResponse("", Usage(), self.name, error="MockAgentLLM is an agent backend, not a text model")


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
            d = extract_json(resp.text)
        except ValueError:
            d = {"thought": resp.text[:500], "tool_calls": []}
        calls = tuple(ToolCall(rt.next_call_id(), str(c.get("name")), dict(c.get("args") or {}))
                      for c in (d.get("tool_calls") or []) if isinstance(c, dict))
        return Message("assistant", str(d.get("thought", ""))[:2000], tool_calls=calls)
