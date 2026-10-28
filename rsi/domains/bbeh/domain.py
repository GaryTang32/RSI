"""BBEH harness domain: :class:`~rsi.domains.bbh.BBHDomain` (hence AgentQA's harness interface) with BBEH's grader.

Same seed harness as BBH (one call, the question plus "End with a final line `ANSWER: <answer>`"), same feedback
wording and grader-verdict markers, same ``domain_of`` (the task family). Different: :meth:`grade` (our ``ANSWER:``
extraction, then the official BBEH pipeline, :mod:`.grading`), leakage terms (targets of more than 2 tokens), and no
``family_hint`` / cot3 reference (BBEH publishes no CoT prompts).
"""
from __future__ import annotations

import sys
from typing import Optional

from ...core.tasks import TaskSuite
from ..bbh import domain as _bbh_domain
from ..bbh.domain import BBHDomain, BBHSimModel
from . import grading as _grading
from .data import BBEH
from .grading import extract_answer, is_correct, is_generic_target

DEFAULT_OOD_HINT = ("The harness will later be run unchanged on other kinds of long, multi-step reasoning questions "
                    "with exact answers (counting, tracking, ordering, multiple choice), so improvements must be "
                    "general.")


class BBEHDomain(BBHDomain):
    """BIG-Bench Extra Hard questions through an AgentQA-style harness, graded like ``bbeh/evaluate.py``."""

    name = "bbeh"
    answer_note = ("the comparison is case-insensitive, ignores spaces after commas, bold and LaTeX wrappers, and "
                   "takes only the first line of the answer. ")
    _is_correct = staticmethod(is_correct)
    _extract = staticmethod(extract_answer)
    _is_generic = staticmethod(is_generic_target)

    def __init__(self, tasks: Optional[TaskSuite] = None, *, tool_timeout_s: float = 10.0,
                 ood_hint: Optional[str] = None, family_hint: bool = False) -> None:
        if family_hint:
            raise ValueError("family_hint exists for the BBH cot3 reference; BBEH has no official CoT prompts")
        if tasks is None:
            from ..bbh.suite import make_suite
            tasks = make_suite(benchmark=BBEH)
        super().__init__(tasks, tool_timeout_s=tool_timeout_s, ood_hint=ood_hint or DEFAULT_OOD_HINT)

    def _restore_grading(self) -> list[str]:
        return _bbh_domain._restore_integrity() + _restore_integrity()


#: AgentQA SimModel kind per BBEH family (offline only; unknown families: 0.5)
SIM_KIND = {"multistep_arithmetic": "numeric", "boolean_expressions": "numeric", "time_arithmetic": "dates",
            "word_sorting": "strings", "web_of_lies": "numeric", "causal_understanding": "lists",
            "object_counting": "numeric", "temporal_sequence": "dates", "shuffled_objects": "numeric",
            "dyck_languages": "numeric", "geometric_shapes": "lists", "hyperbaton": "strings"}


class BBEHSimModel(BBHSimModel):
    """AgentQA's simulated frozen model on BBEH questions (offline tests and dry runs only)."""

    sim_kind = SIM_KIND
    prefix = "bbeh"


def _integrity_targets() -> list:
    mod = sys.modules[__name__]
    return ([(_grading, n) for n in ("is_correct", "answer_text", "strip_latex", "official_extract_answer",
                                     "fuzzy_match", "preprocess_sample", "preprocess_reference",
                                     "evaluate_correctness", "extract_answer")]
            + [(mod, "_restore_integrity")]
            + [(BBEHDomain, n) for n in ("_restore_grading", "_is_correct", "_extract", "_is_generic")])


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
