"""AgentQA: a harness-evolution domain with exact graders.

The artifact is an *agent harness* around a frozen model:

* ``harness.py`` defines ``solve(question, llm, tools, files) -> str``
  (control flow: when to call the model, when to use tools, how to verify);
* ``prompts/*.md`` hold the system/task prompts;
* optional ``skills/*.md``, ``memory/*.md``, ``tools/*.py`` - whatever a proposer adds.

``llm(prompt, system=None) -> str`` is the frozen task model (metered: every
call is a step and its tokens are the harness's inference cost).
``tools.python(code) -> str`` runs code in a sandboxed subprocess.

Grading is exact and lives outside the artifact.
"""
from __future__ import annotations

import hashlib
import random
import re
import threading
import types
from pathlib import Path
from typing import Optional

from ...core.artifact import Artifact
from ...core.domain import Domain, Execution
from ...core.llm import LLM, MockLLM
from ...core.sandbox import run_python
from ...core.tasks import Task, TaskSuite
from .generators import make_suite

SEED_HARNESS_DIR = Path(__file__).parent / "seed_harness"

MAX_LLM_CALLS = 12
MAX_TOOL_CALLS = 8


class HarnessBudgetExceeded(RuntimeError):
    pass


class Tools:
    def __init__(self, timeout_s: float = 10.0) -> None:
        self.timeout_s = timeout_s
        self.calls = 0
        self.log: list[str] = []

    def python(self, code: str) -> str:
        """Run Python code in a subprocess; returns stdout (+ stderr tail on error)."""
        self.calls += 1
        if self.calls > MAX_TOOL_CALLS:
            raise HarnessBudgetExceeded("too many tool calls")
        rr = run_python(code, timeout_s=self.timeout_s, mem_mb=1024)
        out = rr.stdout.strip()
        if not rr.ok:
            out = (out + "\n" + rr.stderr.strip()[-800:]).strip()
        self.log.append(f"[python]\n{code[:1500]}\n[output]\n{out[:1500]}")
        return out[:4000]


_NUM = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def extract_answer(output: str) -> str:
    text = str(output or "").strip()
    m = re.findall(r"ANSWER\s*[:=]\s*(.+)", text, flags=re.I)
    if m:
        return m[-1].strip()
    lines = [l for l in text.splitlines() if l.strip()]
    return lines[-1].strip() if lines else ""


def normalize(s: str) -> str:
    s = str(s).strip().strip("`*\"' ").rstrip(".").strip()
    s = s.replace("$", "")
    return re.sub(r"\s+", " ", s).lower()


def is_correct(target: str, output: str) -> bool:
    ans = normalize(extract_answer(output))
    tgt = normalize(target)
    if re.fullmatch(r"-?\d+(?:\.\d+)?", tgt):
        nums = _NUM.findall(ans)
        if not nums:
            return False
        try:
            return abs(float(nums[-1].replace(",", "")) - float(tgt)) < 1e-6
        except ValueError:
            return False
    return ans == tgt or ans.endswith(" " + tgt) or ans.endswith(":" + tgt)


class AgentQADomain(Domain):
    name = "agentqa"
    components = {
        "prompt": ["prompts/*"],
        "control_flow": ["harness.py"],
        "tool": ["tools/*"],
        "skill": ["skills/*"],
        "memory": ["memory/*"],
        "subagent": ["agents/*"],
    }
    structural_components = ("tool", "skill", "memory", "subagent")

    def __init__(self, tasks: Optional[TaskSuite] = None, *, tool_timeout_s: float = 10.0) -> None:
        super().__init__(tasks or make_suite())
        self.tool_timeout_s = tool_timeout_s

    # ---- artifact
    @staticmethod
    def seed_artifact() -> Artifact:
        return Artifact.from_dir(SEED_HARNESS_DIR)

    def describe(self) -> str:
        fams = self.tasks.families("evolve")
        return (
            "The artifact is an LLM agent harness. harness.py defines solve(question, llm, tools, files) -> str, "
            "where llm(prompt, system=None) -> str calls a frozen language model (each call costs tokens), "
            "tools.python(code) -> str runs Python in a sandbox and returns stdout, and files maps artifact file "
            "paths to their text (prompts, skills, memory). The returned string is graded by exact match of the "
            "final answer (a line 'ANSWER: <value>' is extracted if present, else the last line). "
            f"Practice tasks are short questions of these kinds: {fams}. The harness will later be run unchanged "
            "on other kinds of short questions with exact answers (dates, text manipulation, list statistics, "
            "number theory), so improvements must be general."
        )

    # ---- execution
    def execute(self, artifact: Artifact, task: Task, *, seed: int, llm: Optional[LLM]) -> Execution:
        if llm is None:
            raise ValueError("AgentQA needs a task LLM")
        src = artifact.get("harness.py")
        if src is None:
            return Execution(error="harness.py missing")
        tools = Tools(self.tool_timeout_s)
        trace: list[str] = []
        state = {"calls": 0, "tokens": 0, "usd": 0.0}
        lock = threading.Lock()

        def call_llm(prompt: str, system: Optional[str] = None) -> str:
            with lock:
                state["calls"] += 1
                i = state["calls"]
            if i > MAX_LLM_CALLS:
                raise HarnessBudgetExceeded("too many LLM calls")
            resp = llm.complete(str(prompt), system=system, seed=seed * 1000 + i, role="task")
            if not resp.ok:
                raise RuntimeError(f"infra: llm backend error: {resp.error}")
            with lock:
                state["tokens"] += resp.usage.total_tokens
                state["usd"] += resp.usage.cost_usd
            trace.append(f"[llm call {i}]\nSYSTEM: {(system or '')[:600]}\nPROMPT: {str(prompt)[:2000]}\n"
                         f"REPLY: {resp.text[:2000]}")
            return resp.text

        mod = types.ModuleType("harness_candidate")
        mod.__dict__["__file__"] = "harness.py"
        try:
            exec(compile(src, "harness.py", "exec"), mod.__dict__)  # noqa: S102 - harness code is the artifact
            solve = mod.__dict__.get("solve")
            if not callable(solve):
                return Execution(error="harness.py defines no solve()")
            out = solve(task.input, call_llm, tools, artifact.files)
        except Exception as e:  # noqa: BLE001
            # Backend failures are tagged "infra:" so the evaluator counts them as missing trials.
            err = str(e) if str(e).startswith("infra:") else f"{type(e).__name__}: {e}"
            return Execution(error=err, trace="\n".join(trace + tools.log), tokens=state["tokens"],
                             cost_usd=state["usd"], steps=state["calls"] + tools.calls)
        return Execution(output=str(out) if out is not None else "", trace="\n".join(trace + tools.log),
                         tokens=state["tokens"], cost_usd=state["usd"], steps=state["calls"] + tools.calls,
                         meta={"llm_calls": state["calls"], "tool_calls": tools.calls})

    def grade(self, task: Task, execution: Execution) -> tuple[float, str]:
        ok = is_correct(str(task.target), str(execution.output))
        got = extract_answer(str(execution.output))[:200]
        if ok:
            return 1.0, f"Correct (answer {got!r})."
        return 0.0, f"Incorrect. Extracted answer {got!r}; expected {task.target!r}."


# ------------------------------------------------------------------ simulated model
BASE_ACC = {"numeric": 0.4, "dates": 0.15, "numbertheory": 0.05, "strings": 0.8, "lists": 0.8, "arith": 0.9,
            "units": 0.95}
#: simulated reasoning tokens when answering directly (strings/lists need long reasoning)
DIRECT_TOKENS = {"strings": 3000, "lists": 2500, "numeric": 700, "numbertheory": 700, "dates": 600}


def _h(*parts) -> random.Random:
    return random.Random(int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:16], 16))


def _wrong(ans: str, rng: random.Random) -> str:
    if re.fullmatch(r"-?\d+", ans):
        v = int(ans)
        return str(v + rng.choice([-1, 1]) * rng.choice([1, 2, 10, 100, 1000, max(1, abs(v) // 7)]))
    chars = list(ans)
    rng.shuffle(chars)
    return "".join(chars) + ("x" if "".join(chars) == ans else "")


class SimModel(MockLLM):
    """A simulated frozen model for AgentQA that rewards *generic* harness skills.

    * asked to write Python for a known question -> emits correct code with p=0.93;
    * shown tool output ("OUTPUT" / "Tool output" / "Result:") -> reports it as the answer;
    * otherwise answers directly with family accuracy, +0.12 when asked to reason
      step by step, +0.05 when asked to verify; formats "ANSWER: x" only if the
      prompt asks for that format. Verbose reasoning costs extra tokens.

    ``skill`` scales all accuracies (weaker/stronger model families for
    cross-model transfer tests).
    """

    def __init__(self, suite: TaskSuite, skill: float = 1.0, name: str = "sim-model") -> None:
        self.questions = {t.input: (str(t.target), t.family) for t in suite.tasks.values()}
        self.skill = skill
        super().__init__(self._respond, name=name)

    def _find(self, prompt: str):
        for q, v in self.questions.items():
            if q in prompt:
                return q, v
        # fall back to fuzzy: the longest question sharing its first 60 chars
        for q, v in self.questions.items():
            if q[:60] in prompt:
                return q, v
        return None, None

    def _respond(self, prompt: str, system: Optional[str], seed, i) -> str:
        full = (system or "") + "\n" + prompt
        low = full.lower()
        rng = _h(prompt, system, seed)
        q, info = self._find(prompt)
        tool_out = re.split(r"(?:tool output|python output|\boutput\b|result)\s*[:=]\s*", prompt, flags=re.I)
        if len(tool_out) > 1 and tool_out[-1].strip():
            line = tool_out[-1].strip().splitlines()[0].strip()
            return f"The tool returned {line}.\nANSWER: {line}"
        if info is None:
            return "I am not sure what you are asking.\nANSWER: unknown"
        ans, fam = info
        wants_code = ("```python" in low) or ("python code" in low) or ("write python" in low) or \
                     ("python program" in low)
        if wants_code:
            p = 0.93 * min(1.0, self.skill + 0.1)
            val = ans if rng.random() < p else _wrong(ans, rng)
            return f"Here is code that computes it.\n```python\nprint({val!r})\n```"
        p = BASE_ACC.get(fam, 0.5) * self.skill
        verbose = "step by step" in low or "think" in low or "reason" in low
        if verbose:
            p += 0.12
        if "verify" in low or "double-check" in low or "check your" in low:
            p += 0.05
        p = min(p, 0.95)
        val = ans if rng.random() < p else _wrong(ans, rng)
        body = "(thinking) " + "x" * 4 * DIRECT_TOKENS.get(fam, 400) + "\n"
        if verbose:
            body = "Let me work through this step by step. " + " ".join(
                f"Step {k}: consider the quantities involved and combine them carefully." for k in range(1, 9)) + "\n"
        if "answer:" in low:
            return f"{body}ANSWER: {val}"
        return f"{body}The answer is {val}." if rng.random() < 0.7 else f"{body}{val}"
