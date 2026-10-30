"""BBH harness domain: AgentQA's harness interface, execution sandbox and budgets, with BBH tasks and grading.

:class:`BBHDomain` subclasses :class:`~rsi.domains.agentqa.AgentQADomain`, so every method runs on it exactly as on
AgentQA: the same ``solve(question, llm, tools, files)`` contract, ``rollout`` / ``grade_execution`` / ``run``,
``validate_artifact``, ``harness_brief`` (no grading words, for Harness²), ``grader_verdict_markers`` (the feedback
keeps AgentQA's exact wording, so the markers are the same), and ``domain_of(task)`` = the BBH task family (the
Harness² library key). What changes: the tasks, :meth:`grade` (BBH answer conventions, :mod:`.grading`),
:meth:`describe`, :meth:`leakage_terms` (generic targets such as ``(A)`` or ``True`` are not leakage terms), and the
seed harness (one call, asked to end with an ``ANSWER:`` line).
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path
from typing import Optional

from ...core.artifact import Artifact
from ...core.domain import Execution
from ...core.llm import LLM, artifact_usage
from ...core.tasks import Task, TaskSuite
from ..agentqa import domain as _aq
from ..agentqa.domain import AgentQADomain, SimModel
from . import grading as _grading
from .data import load_cot_prompt
from .grading import extract_answer, is_correct, is_generic_target
from .suite import make_suite

SEED_HARNESS_DIR = Path(__file__).parent / "seed_harness"
#: file the domain adds to ``files`` when ``family_hint=True`` (only the fixed CoT reference arm uses it)
FAMILY_FILE = "_task/family.txt"

DEFAULT_OOD_HINT = ("The harness will later be run unchanged on other kinds of reasoning questions with exact "
                    "answers (multiple choice, yes/no, counting, sequences), so improvements must be general.")


class BBHDomain(AgentQADomain):
    """BIG-Bench Hard questions run through an AgentQA-style harness; graded by :func:`.grading.is_correct`.

    ``family_hint=True`` adds the task's family name to the harness's ``files`` as ``_task/family.txt``. It exists for
    the fixed few-shot reference (:func:`cot3_artifact`, which needs the family's exemplars, as in the BBH paper);
    searched harnesses run with the default ``False``, like on AgentQA."""

    name = "bbh"
    #: answer-format sentence of :meth:`describe`
    answer_note = "multiple-choice questions are answered with the option letter in parentheses, e.g. (B). "
    #: grading functions (staticmethods, bound at class creation: replacing the module attribute changes nothing)
    _is_correct = staticmethod(is_correct)
    _extract = staticmethod(extract_answer)
    _is_generic = staticmethod(is_generic_target)

    def __init__(self, tasks: Optional[TaskSuite] = None, *, tool_timeout_s: float = 10.0,
                 ood_hint: Optional[str] = None, family_hint: bool = False) -> None:
        super().__init__(tasks if tasks is not None else make_suite(), tool_timeout_s=tool_timeout_s,
                         ood_hint=ood_hint or DEFAULT_OOD_HINT)
        self.family_hint = family_hint

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
            "final answer (a line 'ANSWER: <value>' is extracted if present, else the last line); "
            + self.answer_note + f"Practice tasks are multi-step reasoning questions of these kinds: {fams}. " + (self.ood_hint or ""))

    def domain_of(self, task: Task) -> str:
        """Harness² library key: the BBH task family."""
        return str(task.family)

    def leakage_terms(self, split: str = "evolve") -> list[str]:
        """Task ids and the non-generic targets of ``split`` (full word lists, long bracket sequences)."""
        terms = []
        for t in self.tasks.split(split, allow_sealed=True):
            terms.append(str(t.id))
            if t.target is not None and not self._is_generic(str(t.target)):
                terms.append(str(t.target))
        return sorted(set(terms))

    def execute(self, artifact: Artifact, task: Task, *, seed: int, llm: Optional[LLM]) -> Execution:
        if self.family_hint:
            artifact = artifact.with_files({FAMILY_FILE: str(task.family)})
        counter = _OutputTokens(llm) if llm is not None else None
        ex = super().execute(artifact, task, seed=seed, llm=counter)
        if counter is not None:     # answer length: SoL-Pi's objective, reported per example by the experiment
            ex.meta = {**(ex.meta or {}), "output_tokens": counter.output_tokens}
        if tampered := self._restore_grading():
            return Execution(error=f"tamper: the harness replaced part of the grading path ({', '.join(tampered)}); "
                                   "restored", trace=ex.trace, tokens=ex.tokens, cost_usd=ex.cost_usd, steps=ex.steps)
        return ex

    def _restore_grading(self) -> list[str]:
        """Put back replaced grading-path attributes (BBH's tripwire; subclasses add theirs)."""
        return _restore_integrity()

    def grade(self, task: Task, execution: Execution) -> tuple[float, str]:
        _aq._restore_integrity()
        self._restore_grading()
        ok = self._is_correct(str(task.target), str(execution.output), str(task.input))
        got = self._extract(str(execution.output))[:200]
        if ok:
            return 1.0, f"Correct (answer {got!r})."
        return 0.0, f"Incorrect. Extracted answer {got!r}; expected {task.target!r}."


class _OutputTokens:
    """Pass-through to the task LLM that sums the output tokens of the calls one rollout makes (a cache replay
    reports the original call's usage). Only ``complete`` is used by the AgentQA executor."""

    def __init__(self, llm: LLM) -> None:
        self._llm = llm
        self._lock = threading.Lock()
        self.output_tokens = 0

    def complete(self, prompt, **kw):
        resp = self._llm.complete(prompt, **kw)
        if resp.ok:
            n = artifact_usage(resp).output_tokens
            with self._lock:
                self.output_tokens += int(n)
        return resp

    def __getattr__(self, name):
        return getattr(self._llm, name)


# ----- offline task model -------------------------------------------------------------------------------------------

#: AgentQA SimModel kind per BBH family (sets the simulated direct-answer accuracy; unknown families: 0.5)
SIM_KIND = {"multistep_arithmetic_two": "numeric", "word_sorting": "strings", "dyck_languages": "numeric",
            "logical_deduction_five_objects": "numeric", "tracking_shuffled_objects_five_objects": "numeric",
            "date_understanding": "dates", "object_counting": "lists", "navigate": "numeric",
            "temporal_sequences": "strings", "geometric_shapes": "numeric", "penguins_in_a_table": "lists",
            "web_of_lies": "numeric"}


class BBHSimModel(SimModel):
    """AgentQA's simulated frozen model on BBH questions (offline tests and dry runs only)."""

    sim_kind = SIM_KIND
    prefix = "bbh"

    def __init__(self, suite: TaskSuite, skill: float = 1.0, name: Optional[str] = None) -> None:
        p = self.prefix
        super().__init__(suite, skill=skill, name=name or (f"{p}-sim-model" if skill == 1.0 else f"{p}-sim@{skill:g}"))
        self.questions = {q: (ans, self.sim_kind.get(fam, fam)) for q, (ans, fam) in self.questions.items()}


# ----- fixed reference: the official 3-shot CoT prompt per family ---------------------------------------------------

COT_HARNESS = '''"""Fixed reference: the official BBH 3-shot chain-of-thought prompt of the question's family, one call."""
import re


def solve(question, llm, tools, files):
    family = files.get("_task/family.txt", "").strip()
    shots = files.get("cot/" + family + ".txt", "")
    prompt = (shots.strip() + "\\n\\n" if shots else "") + "Q: " + question + "\\nA: Let's think step by step."
    reply = llm(prompt)
    found = re.findall(r"answer is\\s*:?\\s*(.+?)\\s*$", reply, flags=re.I | re.M)
    return reply.strip() + ("\\nANSWER: " + found[-1].strip().rstrip(".").strip() if found else "")
'''


def cot3_artifact(families, **load_kw) -> Artifact:
    """Harness with the official CoT prompt (canary header stripped) of each family; run it with ``family_hint``."""
    files = {"harness.py": COT_HARNESS}
    for fam in families:
        files[f"cot/{fam}.txt"] = load_cot_prompt(fam, **load_kw) + "\n"
    return Artifact(files, {"name": "cot3", "fixed_reference": True})


# ----- grading-path integrity (BBH additions to AgentQA's tripwire) ------------------------------------------------

def _integrity_targets() -> list:
    mod = sys.modules[__name__]
    return ([(_grading, n) for n in ("is_correct", "grade_answer", "answer_kind", "parse_options", "_choice_letter",
                                     "_binary_ok", "_number_ok", "_dyck_ok", "_text_ok", "_tokens", "extract_answer",
                                     "normalize")]
            + [(mod, n) for n in ("is_correct", "extract_answer", "_restore_integrity")]
            + [(BBHDomain, n) for n in ("grade", "execute", "domain_of", "leakage_terms", "_restore_grading",
                                        "_is_correct", "_extract", "_is_generic")])


def _restore_integrity() -> list[str]:
    changed = []
    for owner, name, orig in _INTEGRITY:
        if getattr(owner, name, None) is not orig:
            try:
                setattr(owner, name, orig)
            except (AttributeError, TypeError):
                pass
            changed.append(f"{getattr(owner, '__name__', type(owner).__name__)}.{name}")
    return changed


_INTEGRITY = tuple((owner, name, getattr(owner, name)) for owner, name in _integrity_targets())
