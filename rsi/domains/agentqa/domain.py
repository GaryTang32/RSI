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

The harness runs in-process (in its own thread, so the evaluator is never blocked):

* a watchdog stops a harness that spends more than ``harness_timeout_s`` seconds
  (default 60) of *its own* time - time inside model and tool calls does not
  count - so an infinite loop is a graded failure instead of a hung evaluation;
* the grading path (``is_correct`` & co., ``AgentQADomain.grade``, ``Domain.run``,
  the Evaluator's aggregation, and the ``re``/builtin functions the grader uses)
  is snapshotted at import. A harness that replaces any of them is graded as a
  failure (``tamper: ...``) and the originals are restored, so it cannot corrupt
  the grading of later trials. This is a tripwire against reward hacking, not a
  security boundary: in-process code can still read the caller's objects.
"""
from __future__ import annotations

import builtins
import ctypes
import hashlib
import random
import re
import sys
import threading
import time
import types
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

from ...core import domain as _core_domain
from ...core import evaluate as _core_evaluate
from ...core.artifact import Artifact
from ...core.domain import Domain, Execution
from ...core.llm import LLM, MockLLM, artifact_usage
from ...core.sandbox import run_python
from ...core.tasks import Task, TaskSuite
from .generators import make_suite

SEED_HARNESS_DIR = Path(__file__).parent / "seed_harness"

MAX_LLM_CALLS = 12
MAX_TOOL_CALLS = 8
#: Seconds a harness may spend in its own code (outside model and tool calls) before it is stopped.
HARNESS_TIMEOUT_S = 60.0


class HarnessBudgetExceeded(RuntimeError):
    pass


class HarnessTimeout(BaseException):
    """Raised inside a harness that exceeded its own-time budget. A BaseException,
    so an ``except Exception`` in harness code cannot swallow it."""


class _CallClock:
    """Wall time spent inside model/tool calls (the rest of a trial is the harness's own time)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._done = 0.0
        self._open: dict[int, float] = {}
        self._n = 0

    @contextmanager
    def call(self):
        with self._lock:
            self._n += 1
            key = self._n
            self._open[key] = time.monotonic()
        try:
            yield
        finally:
            with self._lock:
                self._done += time.monotonic() - self._open.pop(key)

    def busy(self) -> float:
        now = time.monotonic()
        with self._lock:
            return self._done + sum(now - t for t in self._open.values())


def _async_raise(th: threading.Thread) -> None:
    if th.ident is not None and th.is_alive():
        ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_ulong(th.ident), ctypes.py_object(HarnessTimeout))


def _reap(th: threading.Thread, tries: int = 60) -> None:
    """Keep raising HarnessTimeout in a timed-out harness thread until it ends (it may swallow a few)."""
    for _ in range(tries):
        if not th.is_alive():
            return
        _async_raise(th)
        th.join(0.5)


class Tools:
    def __init__(self, timeout_s: float = 10.0) -> None:
        self.timeout_s = timeout_s
        self.calls = 0
        self.log: list[str] = []
        self._clock: Optional[_CallClock] = None
        self._dead = False

    def python(self, code: str) -> str:
        """Run Python code in a subprocess; returns stdout (+ stderr tail on error)."""
        if self._dead:
            raise HarnessBudgetExceeded("harness timed out")
        self.calls += 1
        if self.calls > MAX_TOOL_CALLS:
            raise HarnessBudgetExceeded("too many tool calls")
        if self._clock is not None:
            with self._clock.call():
                rr = run_python(code, timeout_s=self.timeout_s, mem_mb=1024)
        else:
            rr = run_python(code, timeout_s=self.timeout_s, mem_mb=1024)
        out = rr.stdout.strip()
        if not rr.ok:
            out = (out + "\n" + rr.stderr.strip()[-800:]).strip()
        self.log.append(f"[python]\n{code[:1500]}\n[output]\n{out[:1500]}")
        return out[:4000]


_NUM = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def extract_answer(output: str) -> str:
    """The text after the last ``ANSWER:`` (or ``ANSWER =``, any case, markdown
    bold allowed around the label: ``**ANSWER**: x``, ``**ANSWER:** x``); else the
    last non-empty line."""
    text = str(output or "").strip()
    m = re.findall(r"ANSWER\s*\**\s*[:=]\s*\**\s*(.+)", text, flags=re.I)
    if m:
        return m[-1].strip()
    lines = [l for l in text.splitlines() if l.strip()]
    return lines[-1].strip() if lines else ""


def normalize(s: str) -> str:
    """Canonical answer text: unwraps ``\\boxed{x}``, markdown bold and inline
    code, strips surrounding quotes/asterisks/backticks and trailing periods
    (repeatedly), drops ``$``, maps the unicode minus to ``-``, collapses
    whitespace and lower-cases."""
    s = str(s)
    s = re.sub(r"\\boxed\{([^{}]*)\}", r"\1", s)
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)
    s = re.sub(r"`([^`]*)`", r"\1", s)
    s = s.replace("\u2212", "-").replace("$", "")
    prev = None
    while prev != s:
        prev = s
        s = s.strip().strip("`*\"' ").rstrip(".").strip()
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
    #: own-time budget of one harness run in seconds (model and tool calls excluded); see the module docstring
    harness_timeout_s: float = HARNESS_TIMEOUT_S

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
        clock = _CallClock()
        tools = Tools(self.tool_timeout_s)
        tools._clock = clock
        trace: list[str] = []
        state = {"calls": 0, "tokens": 0, "usd": 0.0, "dead": False}
        lock = threading.Lock()

        def call_llm(prompt: str, system: Optional[str] = None) -> str:
            with lock:
                if state["dead"]:
                    raise HarnessBudgetExceeded("harness timed out")
                state["calls"] += 1
                i = state["calls"]
            if i > MAX_LLM_CALLS:
                raise HarnessBudgetExceeded("too many LLM calls")
            with clock.call():
                resp = llm.complete(str(prompt), system=system, seed=seed * 1000 + i, role="task")
            if not resp.ok:
                msg = f"infra: llm backend error: {resp.error}"
                with lock:
                    state.setdefault("infra", msg)   # remembered even if the harness swallows the exception
                raise RuntimeError(msg)
            u = artifact_usage(resp)   # a cache replay reports the original call's cost, like a fresh run
            with lock:
                state["tokens"] += u.total_tokens
                state["usd"] += u.cost_usd
            trace.append(f"[llm call {i}]\nSYSTEM: {(system or '')[:600]}\nPROMPT: {str(prompt)[:2000]}\n"
                         f"REPLY: {resp.text[:2000]}")
            return resp.text

        box: dict = {}
        # The harness thread gets only what the harness may see (no Task object: its target is the answer).
        th = threading.Thread(target=_run_harness, args=(src, task.input, call_llm, tools, artifact.files, box),
                              name="agentqa-harness", daemon=True)
        t0 = time.monotonic()
        th.start()
        timed_out = False
        while True:
            th.join(0.05)
            if not th.is_alive():
                break
            if time.monotonic() - t0 - clock.busy() > self.harness_timeout_s:
                timed_out = True
                with lock:
                    state["dead"] = True
                tools._dead = True
                threading.Thread(target=_reap, args=(th,), daemon=True, name="agentqa-reaper").start()
                break
        tampered = _restore_integrity()

        def done(**kw) -> Execution:
            with lock:
                toks, usd = state["tokens"], state["usd"]
            return Execution(trace="\n".join(list(trace) + list(tools.log)), tokens=toks, cost_usd=usd,
                             steps=state["calls"] + tools.calls, **kw)

        if tampered:
            return done(error=f"tamper: the harness replaced part of the grading path ({', '.join(tampered)}); "
                              "restored")
        if timed_out:
            return done(error=f"HarnessTimeout: the harness ran more than {self.harness_timeout_s:g} s of its own "
                              "time (outside model and tool calls)")
        if "no_solve" in box:
            return Execution(error="harness.py defines no solve()")
        if "exc" in box:
            e = box["exc"]
            # Backend failures are tagged "infra:" so the evaluator counts them as missing trials.
            err = str(e) if str(e).startswith("infra:") else f"{type(e).__name__}: {e}"
            return done(error=state.get("infra", err))
        if "infra" in state:  # the harness caught a backend failure: still a missing trial, not a wrong answer
            return done(error=state["infra"])
        out = box.get("out")
        return done(output=str(out) if out is not None else "",
                    meta={"llm_calls": state["calls"], "tool_calls": tools.calls})

    def grade(self, task: Task, execution: Execution) -> tuple[float, str]:
        _restore_integrity()   # grade with the original functions even if a concurrent harness replaced them
        ok = is_correct(str(task.target), str(execution.output))
        got = extract_answer(str(execution.output))[:200]
        if ok:
            return 1.0, f"Correct (answer {got!r})."
        return 0.0, f"Incorrect. Extracted answer {got!r}; expected {task.target!r}."


def _run_harness(src: str, question, call_llm, tools: Tools, files: dict, box: dict) -> None:
    """Body of the harness thread: import harness.py and call solve(); results go to ``box``."""
    try:
        mod = types.ModuleType("harness_candidate")
        mod.__dict__["__file__"] = "harness.py"
        exec(compile(src, "harness.py", "exec"), mod.__dict__)  # noqa: S102 - harness code is the artifact
        solve = mod.__dict__.get("solve")
        if not callable(solve):
            box["no_solve"] = True
            return
        box["out"] = solve(question, call_llm, tools, files)
    except BaseException as e:  # noqa: BLE001 - incl. SystemExit and HarnessTimeout: a graded failure
        box["exc"] = e


# ------------------------------------------------------------ grading-path integrity
def _integrity_targets() -> list:
    mod = sys.modules[__name__]
    return ([(mod, n) for n in ("is_correct", "extract_answer", "normalize", "_NUM", "_restore_integrity")]
            + [(AgentQADomain, n) for n in ("grade", "execute", "run")]
            + [(_core_domain.Domain, "run"), (_core_domain, "Trial"),
               (_core_evaluate.Evaluator, "run_one"), (_core_evaluate.Evaluator, "evaluate"),
               (_core_evaluate.EvalResult, "task_scores"), (_core_evaluate.EvalResult, "score"),
               (_core_evaluate.EvalResult, "S")]
            + [(re, n) for n in ("findall", "fullmatch", "sub", "match", "search", "compile")]
            + [(builtins, n) for n in ("float", "abs", "str", "len", "isinstance", "repr")])


def _restore_integrity() -> list[str]:
    """Put back any replaced grading-path attribute; return the names that had been replaced."""
    changed = []
    for owner, name, orig in _INTEGRITY:
        if getattr(owner, name, None) is not orig:
            try:
                setattr(owner, name, orig)
            except (AttributeError, TypeError):
                pass
            changed.append(f"{getattr(owner, '__name__', type(owner).__name__)}.{name}")
    return changed


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

    def __init__(self, suite: TaskSuite, skill: float = 1.0, name: Optional[str] = None) -> None:
        self.questions = {t.input: (str(t.target), t.family) for t in suite.tasks.values()}
        self.skill = skill
        # the name is part of cache identities, so models of different skill must not share it
        super().__init__(self._respond, name=name or ("sim-model" if skill == 1.0 else f"sim-model@{skill:g}"))

    def _find(self, prompt: str):
        for q, v in self.questions.items():
            if q in prompt:
                return q, v
        # fall back to fuzzy: the first question (suite order) whose first 60 chars appear in the prompt
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


# snapshot taken last, so every definition above is included
_INTEGRITY = tuple((owner, name, getattr(owner, name)) for owner, name in _integrity_targets())
