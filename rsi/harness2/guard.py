"""The open-world guard: no grade, rubric or score reaches the loop; grading is possible only after it ends."""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Iterable, Mapping
from typing import Any

from ..core.domain import Domain
from ..core.tasks import Task

#: grader-verdict markers of the reference code [code context.py::GRADER_VERDICT_MARKERS]
SPEC_MARKERS = (
    "deterministic_eval_summary",
    "deterministic_eval_result",
    "judge_scope_and_deterministic_status",
    "Judge findings on the rendered result",
    '"passed_count"',
    '"pass_rate"',
    '"test_pass_rate"',
)
#: keys of rsi's graded records (Trial, EvalResult, transfer reports)
RSI_MARKERS = ('"task_scores"', '"family_scores"', '"vs_reference"', '"unseen_mean"', '"failure_score"')
_GRADED_KEY = re.compile(r'"(score|scores|mean_score)"\s*:')
GRADER_METHODS = ("grade", "run", "grade_execution", "score")
ANSWER_KEYS = re.compile(r"answer|target|expect|solution|gold|truth|label|rubric|grad|score|verdict|reference", re.I)


class OpenWorldViolation(BaseException):
    """Base of the guard's errors: a BaseException, so no ``except Exception`` in a harness or loop swallows it."""


class GraderAccessError(OpenWorldViolation):
    """Something called the domain's grader while the open-world loop was running."""


class GraderLeak(OpenWorldViolation):
    """A grader verdict, score or rubric marker was found in text bound for an improver (a leak, never widened)."""


def markers_for(domain: Domain | None) -> tuple[str, ...]:
    hook = getattr(domain, "grader_verdict_markers", None)
    extra = tuple(hook()) if callable(hook) else ()
    return SPEC_MARKERS + RSI_MARKERS + tuple(m for m in extra if m)


def find_marker(text: str, markers: Iterable[str]) -> str | None:
    """The first grader-verdict marker in ``text``, or None."""
    text = str(text or "")
    hit = next((m for m in markers if m in text), None)
    if hit is None and (m := _GRADED_KEY.search(text)):
        hit = m.group(0)
    return hit


def without_own(text: str, own: Iterable[str] = ()) -> str:
    """``text`` with every verbatim copy of the task's own text cut out (longest first, each cut leaves a newline).

    ``own`` is what the task itself brought (its request, raw input and source files): user data, not a grader
    verdict, so a ``"score":`` key in it is not a leak. Anything the loop generated around it is still screened."""
    text = str(text or "")
    for piece in sorted({str(o) for o in own if o and str(o).strip()}, key=lambda o: (-len(o), o)):
        text = text.replace(piece, "\n")
    return text


#: characters of context, on one side of a marker, that must match the task's own text for the marker to count as a
#: quotation of the task (a trace may quote the task truncated, so a whole verbatim copy is not always present)
QUOTE_CONTEXT = 24


def _occurrences(text: str, markers: Iterable[str]):
    """``(start, end, marker)`` for every grader-verdict marker occurrence in ``text``."""
    for m in markers:
        i = text.find(m)
        while m and i != -1:
            yield i, i + len(m), m
            i = text.find(m, i + 1)
    for mt in _GRADED_KEY.finditer(text):
        yield mt.start(), mt.end(), mt.group(0)


def _quotes_own(text: str, start: int, end: int, own: list[str]) -> bool:
    """True if the marker at ``text[start:end]`` sits in a stretch of the task's own text: QUOTE_CONTEXT characters
    before it, or after it, together with the marker, occur verbatim in ``own``."""
    left = text[max(0, start - QUOTE_CONTEXT) : end]
    right = text[start : end + QUOTE_CONTEXT]
    for snippet, ctx in ((left, text[max(0, start - QUOTE_CONTEXT) : start]), (right, text[end : end + QUOTE_CONTEXT])):
        if len(ctx.strip()) >= QUOTE_CONTEXT // 2 and any(snippet in o for o in own):
            return True
    return False


def check_text(text: str, markers: Iterable[str], where: str, own: Iterable[str] = ()) -> None:
    """Raise :class:`GraderLeak` if ``text`` carries a grader verdict outside the task's own text ``own``.

    Verbatim copies of ``own`` are cut out first; a marker left over still counts as the task's own when the text
    around it is a quotation of ``own`` (e.g. a trace that quotes the task cut off at a length limit)."""
    rest = without_own(text, own)
    pieces = [str(o) for o in own if o and str(o).strip()]
    for start, end, hit in sorted(_occurrences(rest, markers)):
        if not (pieces and _quotes_own(rest, start, end, pieces)):
            raise GraderLeak(f"grader-verdict marker {hit!r} in {where}: this is a leak, not a false positive to widen")


def screen_tree(files: Mapping[str, str | None], markers: Iterable[str], where: str) -> None:
    """Screen file names and contents of a harness tree or library entry."""
    markers = tuple(markers)
    for name, text in files.items():
        check_text(name, markers, f"{where} file name {name!r}")
        check_text(text or "", markers, f"{where}/{name}")


def open_task(task: Task, strip: bool = True) -> Task:
    """The task as the agent may see it: no target and no answer-like meta keys."""
    if not strip:
        return task
    meta = {k: v for k, v in (task.meta or {}).items() if not ANSWER_KEYS.search(str(k))}
    return dataclasses.replace(task, target=None, meta=meta)


def _refuse(name: str):
    def refused(*a: Any, **kw: Any):
        raise GraderAccessError(
            f"domain.{name}() was called inside the Harness² open-world loop; grading is only "
            "allowed after it ends (posthoc_report)"
        )

    return refused


class OpenWorld:
    """Context manager: the domain's grading entry points raise :class:`GraderAccessError` until exit."""

    def __init__(self, domain: Domain) -> None:
        self.domain = domain
        self.markers = markers_for(domain)
        self._saved: dict[str, Any] = {}
        self.active = False

    def __enter__(self) -> OpenWorld:
        own = vars(self.domain)
        for name in GRADER_METHODS:
            if callable(getattr(self.domain, name, None)):
                self._saved[name] = own.get(name, _ABSENT)
                setattr(self.domain, name, _refuse(name))
        self.active = True
        return self

    def __exit__(self, *exc: Any) -> None:
        for name, prev in self._saved.items():
            if prev is _ABSENT:
                vars(self.domain).pop(name, None)
            else:
                setattr(self.domain, name, prev)
        self._saved.clear()
        self.active = False

    def check(self, text: str, where: str, own: Iterable[str] = ()) -> None:
        check_text(text, self.markers, where, own)

    def screen(self, files: Mapping[str, str | None], where: str) -> None:
        screen_tree(files, self.markers, where)


_ABSENT = object()
