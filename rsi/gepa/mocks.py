"""Offline reflection LMs for GEPA on domains other than RuleWorld, and a two-module
AgentQA harness.

* :class:`GenericReflectionLM` - domain-agnostic: appends the first sentence of each
  failing example's feedback as a "Lesson:" line (at most ``max_new`` per call).
  Enough to exercise the full loop on any :class:`rsi.core.Domain` whose grader
  explains failures.
* :class:`AgentQAReflectionLM` - for ``rsi.domains.agentqa`` harness prompts with the
  offline :class:`~rsi.domains.agentqa.SimModel`: adds one generic skill per call
  (exact answer format, step-by-step reasoning, verification, "write Python"),
  chosen from the evidence in the side_info when feedback text is present and at
  random under score-only feedback; it keeps the ``{question}`` placeholder. It also
  answers the RL baseline's brainstorm prompt and the few-shot grounded prompt.
* :func:`two_module_harness` - AgentQA seed artifact with two prompt components
  (``prompts/solver.md`` -> optional Python tool run -> ``prompts/reporter.md``), so
  module-wise merge is meaningful on AgentQA.
"""
from __future__ import annotations

import hashlib
import random
import re
from typing import Optional

from ..core.artifact import Artifact
from ..core.llm import MockLLM


def _rng(*parts) -> random.Random:
    return random.Random(int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:16], 16))


def _split(prompt: str) -> tuple[str, str]:
    parts = prompt.split("```")
    return (parts[1].strip("\n") if len(parts) > 1 else ""), (parts[3] if len(parts) > 3 else "")


def _records(side: str) -> list[dict]:
    recs = []
    for block in re.split(r"^# Example \d+\s*$", side, flags=re.M)[1:]:
        rec = {}
        for sec in re.split(r"^## ", block, flags=re.M)[1:]:
            head, _, body = sec.partition("\n")
            rec[head.strip()] = body.strip()
        recs.append(rec)
    return recs


def _score(fb: str) -> Optional[float]:
    m = re.search(r"Score:\s*(\d+(?:\.\d+)?)", fb or "")
    return float(m.group(1)) if m else None


class GenericReflectionLM(MockLLM):
    """Appends feedback sentences of failing examples as lessons (any domain)."""

    def __init__(self, max_new: int = 1, name: str = "generic-reflect") -> None:
        self.max_new = max_new
        super().__init__(self._respond, name=name)

    def _respond(self, prompt, system, seed, i) -> str:
        cur, side = _split(prompt)
        lines = cur.splitlines()
        added = 0
        for rec in _records(side):
            fb = rec.get("Feedback", "")
            s = _score(fb)
            if s is not None and s >= 1.0:
                continue
            text = re.sub(r"^Score:\s*[0-9.]+\.?\s*", "", fb).strip()
            if not text:
                continue
            first = re.split(r"(?<=[.!?])\s", text)[0].strip()
            lesson = f"Lesson: {first}"
            if lesson not in lines and added < self.max_new:
                lines.append(lesson)
                added += 1
        return "```\n" + "\n".join(lines).strip("\n") + "\n```"


SKILLS = {
    "format": "End your reply with a final line of the form 'ANSWER: <value>'.",
    "step": "Think step by step before answering.",
    "verify": "Verify (double-check) your result before giving the final answer.",
    "python": "Write a short Python program that prints the exact answer, in a fenced python code block.",
}
DISTRACTORS = ["Be concise.", "Be polite and friendly.", "Use British spelling.", "Avoid jargon.",
               "Keep the reply under 200 words.", "Mention your confidence level.", "Restate the question first.",
               "Use bullet points where helpful."]
_SKILL_KEYS = {"format": "answer:", "step": "step by step", "verify": "verify", "python": "python"}


class AgentQAReflectionLM(MockLLM):
    """Mock reflection LM for AgentQA harness prompts (see module docstring)."""

    def __init__(self, name: str = "agentqa-reflect") -> None:
        super().__init__(self._respond, name=name)

    def _respond(self, prompt, system, seed, i) -> str:
        rng = _rng("aq", prompt, seed)
        if prompt.startswith("You are helping to optimize the instruction"):      # RL brainstorm
            lines = list(SKILLS.values()) + DISTRACTORS
            rng.shuffle(lines)
            return "```\n" + "\n".join(lines) + "\n```"
        cur, side = _split(prompt)
        low = cur.lower()
        missing = [k for k, key in _SKILL_KEYS.items() if key not in low]
        if "correct outputs for several example inputs" in prompt:                # few-shot grounded proposal
            order = [k for k in ("format", "step", "verify") if k in missing]
        else:
            recs = _records(side)
            fails = [r for r in recs if (_score(r.get("Feedback", "")) or 0.0) < 1.0]
            text_fb = any("expected" in r.get("Feedback", "").lower() for r in fails)
            if not fails:
                order = []
            elif text_fb:
                garbled = any(re.search(r"extracted answer '([^0-9'-][^']*)'", r.get("Feedback", ""), re.I)
                              for r in fails)
                pref = ["format", "python", "step", "verify"] if garbled else ["python", "step", "verify", "format"]
                order = [k for k in pref if k in missing]
            else:
                order = list(missing)
                rng.shuffle(order)
                if rng.random() < 0.3:
                    order.insert(0, "distractor")
        new = cur.rstrip("\n")
        if order:
            k = order[0]
            line = rng.choice(DISTRACTORS) if k == "distractor" else SKILLS[k]
            new = (new + "\n" + line).strip("\n")
        return "```\n" + new + "\n```"


TWO_MODULE_HARNESS = '''"""Two-module harness: solver call -> optional Python tool run -> reporter call."""
import re


def solve(question, llm, tools, files):
    reply = llm("Question: " + question, system=files.get("prompts/solver.md", ""))
    code = re.findall(r"```python\\n(.*?)```", reply, re.S)
    if code:
        out = tools.python(code[-1]).strip()
        draft = "Tool output: " + (out.splitlines()[-1] if out else "")
    else:
        lines = [l for l in reply.strip().splitlines() if l.strip()]
        draft = "Draft: " + (lines[-1] if lines else "")
    final = llm("Question: " + question + "\\n" + draft, system=files.get("prompts/reporter.md", ""))
    lines = [l for l in final.strip().splitlines() if l.strip()]
    return final if "ANSWER" in final.upper() else (lines[-1] if lines else "")
'''


def two_module_harness() -> Artifact:
    """AgentQA seed artifact with two evolvable prompts (harness.py stays frozen)."""
    return Artifact({"harness.py": TWO_MODULE_HARNESS,
                     "prompts/solver.md": "You are a helpful assistant. Solve the question.\n",
                     "prompts/reporter.md": "Give the final answer to the question.\n"},
                    {"domain": "agentqa", "harness": "two-module"})
