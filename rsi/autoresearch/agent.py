"""Research agents: the LLM agent (an Editor over program.md + results.tsv) and scripted offline agents."""
from __future__ import annotations

import ast
import random
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Sequence

from ..core.artifact import Artifact, parse_file_blocks
from ..core.editors import EDIT_FORMAT, Editor, Proposal
from ..core.llm import LLM, MockLLM

SYSTEM = ("You are an autonomous ML researcher running experiments in a loop. You edit code to improve one "
          "metric under a fixed budget. You never ask the human questions.")


@dataclass
class AgentContext:
    """Everything the agent may read before one experiment."""

    program: str                        # rendered program.md
    artifact: Artifact                  # the current branch tip (all in-scope files)
    editable: tuple[str, ...]
    locked: tuple[str, ...]
    results_tsv: str                    # results.tsv (whole file by default; see Config.history_rows)
    git_log: str                        # kept-commit chain (newest first)
    task_brief: str = ""
    metric: str = "score"
    direction: str = "max"
    best: Optional[float] = None
    experiment: int = 0
    seed: int = 0
    notes: str = ""                     # framework notes (e.g. the NEVER STOP nudge after empty turns)

    def files(self) -> dict[str, str]:
        out = {"results.tsv": self.results_tsv, "git_log.txt": self.git_log}
        if self.notes:
            out["notes.txt"] = self.notes
        return out


class ResearchAgent:
    """Proposes one experiment per turn and may fix a crash."""

    name = "agent"

    def propose(self, ctx: AgentContext) -> Proposal:  # pragma: no cover
        raise NotImplementedError

    def fix_crash(self, ctx: AgentContext, candidate: Artifact, description: str, log_tail: str) -> Optional[Proposal]:
        return None

    def usage(self) -> dict:
        return {}


PROPOSE = """{program}

---
Task: {brief}
Current best {metric}: {best}. This is experiment #{n}.
Propose exactly ONE experiment: edit the editable file(s) with a single idea. Put a short one-line
description of what the experiment tries in "change" (it becomes the results.tsv description,
e.g. "LR 0.003 -> 0.01" or "switch to ReLU"). Return the complete new content of every file you change.
"""

FIX = """{program}

---
The experiment "{desc}" crashed. `tail -n 50 run.log`:
```
{tail}
```
If this is something dumb and easy to fix (a typo, a missing import, a wrong name), fix it while keeping the
idea and return the complete corrected file(s). If the idea itself is fundamentally broken, reply with the
single word GIVE_UP and no file blocks.
"""


_REWIND = re.compile(r"^\s*REWIND\s+([0-9a-f]{7,40})\s*$", re.M)
_TRAILER = re.compile(r"^(```[a-zA-Z]*|Co-Authored-By:.*|Claude-Session:.*|Signed-off-by:.*)\s*$")
_COMMIT_TRAILER = re.compile(r"^(Co-Authored-By:.*|Claude-Session:.*|Signed-off-by:.*)\s*$")


def sanitize_reply_files(prop: Proposal, parent: Artifact) -> Proposal:
    """Strip a trailing lone code fence and commit trailers from changed files (kept in ``meta["sanitized"]``)."""
    if not prop.ok:
        return prop
    ups, removed = {}, {}
    for name, text in prop.artifact.files.items():
        if parent.files.get(name) == text:
            continue
        prose = name.lower().endswith((".md", ".markdown", ".rst", ".txt"))    # a closing fence is legal there
        pat = _COMMIT_TRAILER if prose else _TRAILER
        lines = text.rstrip("\n").split("\n")
        cut = []
        while lines and (not lines[-1].strip() or pat.match(lines[-1].strip())):
            cut.append(lines.pop())
        cut = [c for c in cut if c.strip()]
        if cut and not any(pat.match(l.strip()) for l in parent.files.get(name, "").splitlines()[-3:]):
            ups[name] = "\n".join(lines) + "\n"
            removed[name] = list(reversed(cut))
    if ups:
        prop.artifact = prop.artifact.with_files(ups)
        prop.meta["sanitized"] = removed
    return prop


class LLMResearchAgent(ResearchAgent):
    """Agent over an Editor; ``editable=None`` lets out-of-scope edits reach the loop to run or reject."""

    last_fix: Optional[Proposal] = None                        # last fix reply (trace only)

    def __init__(self, editor: Editor, *, system: str = SYSTEM, name: str = "llm-agent", sanitize: bool = True) -> None:
        self.editor = editor
        self.sanitize = sanitize
        self.system = system
        self.name = name

    def propose(self, ctx: AgentContext) -> Proposal:
        instr = PROPOSE.format(program=ctx.program, brief=ctx.task_brief, metric=ctx.metric,
                               best="n/a" if ctx.best is None else f"{ctx.best:.6f}", n=ctx.experiment)
        prop = self.editor.edit(ctx.artifact, instr, context=ctx.files(), editable=None, system=self.system,
                                seed=ctx.seed * 100003 + ctx.experiment, role="researcher")
        prop.meta.setdefault("prompt", self._prompt_text(ctx.artifact, instr, ctx.files()))
        if not prop.ok:
            m = _REWIND.search(prop.raw or "")
            if m:
                prop.meta["rewind"] = m.group(1)
                prop.change = prop.change or f"REWIND {m.group(1)}"
            return prop
        return sanitize_reply_files(prop, ctx.artifact) if self.sanitize else prop

    def _prompt_text(self, artifact, instr, context) -> str:
        """The exact prompt the editor sent (RewriteEditor), else the instructions."""
        build = getattr(self.editor, "build_prompt", None)
        try:
            return build(artifact, instr, context, None) if build is not None else instr
        except Exception:  # noqa: BLE001 - tracing only
            return instr

    def fix_crash(self, ctx, candidate, description, log_tail):
        instr = FIX.format(program=ctx.program, desc=description, tail=log_tail[-4000:])
        prop = self.editor.edit(candidate, instr, context=None, editable=None, system=self.system,
                                seed=ctx.seed * 100003 + ctx.experiment + 50000, role="researcher_fix")
        prop.meta.setdefault("prompt", self._prompt_text(candidate, instr, None))
        if self.sanitize:
            prop = sanitize_reply_files(prop, candidate)
        self.last_fix = prop
        if not prop.ok or "GIVE_UP" in (prop.raw or "")[:200]:
            return None
        prop.change = prop.change or description
        return prop

    def usage(self) -> dict:
        llm: Optional[LLM] = getattr(self.editor, "llm", None) or getattr(self.editor, "cli", None)
        return llm.meter.snapshot() if llm is not None else {}


@dataclass
class ScriptedEdit:
    """``apply(files) -> (updates, description)`` or None; ``kind``: helpful|neutral|harmful|crash|exploit|unknown."""

    name: str
    kind: str
    apply: Callable[[dict], Optional[tuple[dict, str]]]
    fix: Optional[Callable[[dict], Optional[dict]]] = None    # crash repair that keeps the idea
    group: str = ""                                           # knob / family
    weight: float = 1.0
    meta: dict = field(default_factory=dict)


_KNOB = r"^(?P<name>{name})(?P<eq>\s*=\s*)(?P<val>[^#\n]+?)(?P<rest>\s*(#.*)?)$"


def _knob(text: str, name: str) -> Optional[re.Match]:
    return re.search(_KNOB.format(name=re.escape(name)), text, re.M)


def get_knob(text: str, name: str) -> Any:
    m = _knob(text, name)
    if not m:
        return None
    try:
        return ast.literal_eval(m.group("val").strip())
    except (ValueError, SyntaxError):
        return None


def set_knob(text: str, name: str, value: Any) -> Optional[str]:
    m = _knob(text, name)
    return text[:m.start("val")] + fmt_value(value) + text[m.end("val"):] if m else None


def fmt_value(v: Any) -> str:
    if isinstance(v, float):
        s = f"{v:.6g}"
        return s if any(c in s for c in ".e") else s + ".0"
    return repr(v)


def knob_edit(knob: str, op: Callable[[Any], Any], *, kind: str = "unknown", file: str = "train.py",
              lo: Any = None, hi: Any = None, name: Optional[str] = None, weight: float = 1.0) -> ScriptedEdit:
    """``KNOB = op(KNOB)`` described as ``KNOB old -> new``; not applicable when the result leaves ``[lo, hi]``."""

    def apply(files: dict):
        text = files.get(file)
        old = get_knob(text, knob) if text is not None else None
        if old is None:
            return None
        new = op(old)
        if isinstance(old, int) and not isinstance(old, bool) and isinstance(new, float):
            new = int(round(new))                     # integer knobs stay integers
        if isinstance(new, float):
            new = float(f"{new:.6g}")
        if new == old or (lo is not None and new < lo) or (hi is not None and new > hi):
            return None
        out = set_knob(text, knob, new)
        return ({file: out}, f"{knob} {fmt_value(old)} -> {fmt_value(new)}") if out else None

    return ScriptedEdit(name or f"{knob}:{getattr(op, '__name__', 'op')}", kind, apply, group=knob, weight=weight)


def text_edit(name: str, kind: str, file: str, old: str, new: str, description: str, *,
              fix: Optional[Callable[[dict], Optional[dict]]] = None, group: str = "", weight: float = 1.0,
              extra_files: Optional[dict] = None) -> ScriptedEdit:
    """Replace the first ``old`` with ``new`` in ``file`` (``old=""`` appends); ``extra_files`` overwrite others."""

    def apply(files: dict):
        text = files.get(file)
        if text is None or (old and old not in text) or (not old and new and new in text):
            return None
        out = text.replace(old, new, 1) if old else text + new
        if out == text and not extra_files:
            return None
        ups = {file: out}
        for k, v in (extra_files or {}).items():
            ups[k] = v(files) if callable(v) else v
        return ups, description

    return ScriptedEdit(name, kind, apply, fix=fix, group=group or name, weight=weight)


class MockResearchAgent(ResearchAgent):
    """Scripted agent; ``greedy`` repeats a kept edit w.p. ``p_repeat``, else tries an untried one."""

    def __init__(self, pool: Sequence[ScriptedEdit], *, policy: str = "greedy", seed: int = 0,
                 crash_rate: float = 0.0, exploit_rate: float = 0.0, p_fix: float = 1.0, p_repeat: float = 0.7,
                 schedule: Optional[Sequence[str]] = None, name: str = "mock-agent") -> None:
        self.pool = list(pool)
        self.by_name = {e.name: e for e in self.pool}
        self.policy = "schedule" if schedule is not None else policy
        self.schedule = list(schedule or [])
        self.rng = random.Random(seed)
        self.crash_rate, self.exploit_rate = crash_rate, exploit_rate
        self.p_fix, self.p_repeat = p_fix, p_repeat
        self.name = name
        self._tried: dict[str, set] = {}
        self._last: Optional[ScriptedEdit] = None
        self._last_inc: Optional[str] = None
        self.n_proposals = 0

    def _sub(self, kinds: Sequence[str]) -> list[ScriptedEdit]:
        return [e for e in self.pool if e.kind in kinds]

    def _pick(self, cands: list[ScriptedEdit], files: dict) -> Optional[tuple[ScriptedEdit, dict, str]]:
        cands = list(cands)
        while cands:
            w = [max(e.weight, 1e-9) for e in cands]
            e = self.rng.choices(cands, weights=w)[0]
            r = e.apply(files)
            if r is not None:
                return e, r[0], r[1]
            cands.remove(e)
        return None

    def propose(self, ctx: AgentContext) -> Proposal:
        self.n_proposals += 1
        files = ctx.artifact.files
        inc = ctx.artifact.id
        kept_last = self._last is not None and self._last_inc is not None and inc != self._last_inc
        tried = self._tried.setdefault(inc, set())
        choice = None
        if self.policy == "schedule":
            while self.schedule and choice is None:
                e = self.by_name[self.schedule.pop(0)]
                r = e.apply(files)
                choice = (e, r[0], r[1]) if r else None
            if choice is None:
                return Proposal(None, error="schedule exhausted")
        else:
            u = self.rng.random()
            normal = [e for e in self.pool if e.kind not in ("crash", "exploit")]
            if u < self.exploit_rate and self._sub(["exploit"]):
                choice = self._pick(self._sub(["exploit"]), files)
            elif u < self.exploit_rate + self.crash_rate and self._sub(["crash"]):
                choice = self._pick(self._sub(["crash"]), files)
            elif self.policy == "greedy" and kept_last and self._last.kind not in ("crash", "exploit") \
                    and self.rng.random() < self.p_repeat:
                r = self._last.apply(files)
                choice = (self._last, r[0], r[1]) if r else None
            if choice is None:
                fresh = [e for e in normal if e.name not in tried] if self.policy == "greedy" else normal
                if fresh:
                    choice = self._pick(fresh, files)
                if choice is None:       # out of ideas: combine two edits
                    choice = self._combine(normal, files)
        if choice is None:
            return Proposal(None, error="no applicable edit in the pool")
        e, updates, desc = choice
        tried.add(e.name)
        self._last, self._last_inc = e, inc
        new = ctx.artifact.with_files(updates)
        return Proposal(new, change=desc, hypothesis=f"scripted {e.kind} edit", components=[e.group or e.name],
                        meta={"edit": e.name, "kind": e.kind})

    def _combine(self, normal: list[ScriptedEdit], files: dict):
        first = self._pick(normal, files)
        if first is None:
            return None
        e1, ups1, d1 = first
        merged = dict(files)
        merged.update(ups1)
        second = self._pick([e for e in normal if e.group != e1.group], merged)
        if second is None:
            return first
        e2, ups2, d2 = second
        return e1, {**ups1, **ups2}, f"combine: {d1} + {d2}"

    def fix_crash(self, ctx, candidate, description, log_tail):
        e = self._last
        if e is None or e.fix is None or self.rng.random() >= self.p_fix:
            return None
        ups = e.fix(candidate.files)
        if not ups:
            return None
        return Proposal(candidate.with_files(ups), change=description, hypothesis="trivial fix",
                        meta={"edit": e.name, "kind": e.kind, "fixed": True})


class RandomSearchAgent(ResearchAgent):
    """Baseline: each proposal is ``base`` plus 1..``max_edits`` random applicable edits (independent draws)."""

    def __init__(self, pool: Sequence[ScriptedEdit], base: Artifact, *, seed: int = 0, max_edits: int = 3,
                 name: str = "random-search") -> None:
        self.pool = [e for e in pool if e.kind not in ("crash", "exploit")]
        self.base = base
        self.rng = random.Random(seed)
        self.max_edits = max_edits
        self.name = name

    def propose(self, ctx: AgentContext) -> Proposal:
        files = dict(self.base.files)
        descs = []
        for _ in range(self.rng.randint(1, self.max_edits)):
            cands = list(self.pool)
            self.rng.shuffle(cands)
            for e in cands:
                r = e.apply(files)
                if r:
                    files.update(r[0])
                    descs.append(r[1])
                    break
        if not descs:
            return Proposal(None, error="no applicable edit")
        new = self.base.with_files(files)
        return Proposal(new, change="random: " + "; ".join(descs), meta={"edit": "random", "kind": "random"})


def scripted_llm(pool: Sequence[ScriptedEdit], seed: int = 0, name: str = "scripted-researcher") -> MockLLM:
    """MockLLM answering RewriteEditor prompts with a random applicable pool edit of the artifact in the prompt."""
    rng = random.Random(seed)
    normal = [e for e in pool if e.kind not in ("crash", "exploit")]

    def respond(prompt: str, system, s, i) -> str:
        body = prompt.split("--- CURRENT ARTIFACT ---", 1)[-1].split(EDIT_FORMAT[:40], 1)[0]
        body = body.split("\nEditable files (glob patterns)", 1)[0]
        files = parse_file_blocks(body)
        if "crashed" in prompt and "GIVE_UP" in prompt:
            return "GIVE_UP"
        cands = list(normal)
        while cands:
            e = rng.choice(cands)
            r = e.apply(files)
            if r:
                ups, desc = r
                blocks = "\n".join(f"=== FILE: {k} ===\n{v}" for k, v in ups.items())
                return (f'```json\n{{"change": "{desc}", "hypothesis": "scripted", "components": ["{e.group}"]}}\n```\n'
                        f"{blocks}")
            cands.remove(e)
        return "no idea"

    return MockLLM(respond, name=name)
