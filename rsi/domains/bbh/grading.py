"""Exact-match grading with BIG-Bench Hard's answer conventions (lenient on form, strict on content).

The answer is the text after the last ``ANSWER:`` line, else the last non-empty line (AgentQA's
:func:`~rsi.domains.agentqa.domain.extract_answer`), normalised by AgentQA's :func:`normalize` (unwrap bold, code
and ``\\boxed{}``, strip quotes and trailing periods, lower-case). Then, by the kind of the gold target:

``choice``  ``(A)``..``(R)``: correct iff exactly one option letter is named in parentheses (``(a)``, ``(a) text``)
            and it is the target's; or the answer is the bare letter (``a``, ``a)``, ``option a``); or, with no letter,
            the answer equals the target option's text and no other option has that text. Two letters = wrong.
``binary``  True/False, Yes/No, valid/invalid (any case): the answer equals the target word, or it is short (at most
            8 words), names only the target word of its pair and contains no negation.
``number``  integers: the last number in the answer equals the target (``1,234`` and ``12.0`` accepted); with no
            digits, a number word (zero..twenty).
``dyck``    closing sequences such as ``] ) }``: the trailing run of bracket characters, whitespace removed, equals the
            target; the full sequence (the question's input followed by the target) is also accepted.
``text``    word lists (word_sorting) and the few free-text targets: the answer's tokens (commas, brackets and quotes
            dropped) equal the target's, after an optional lead-in ("the answer is", "sorted list:") or a label
            ending in ``:``. A word list with extra words in front is wrong (no suffix match).
"""
from __future__ import annotations

import re
from typing import Optional

from ..agentqa.domain import extract_answer, normalize

_CHOICE = re.compile(r"\(([A-Z])\)")
BINARY_PAIRS: tuple[frozenset, ...] = (frozenset({"true", "false"}), frozenset({"yes", "no"}),
                                       frozenset({"valid", "invalid"}))
_BRACKETS = set("()[]{}<>")
_NUM = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
_NUM_WORDS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
    "seventeen eighteen nineteen twenty".split())}
_NEGATION = re.compile(r"\b(?:not|never|neither|nor)\b|n't\b")
_OPTION_LINE = re.compile(r"^\s*\(([A-Z])\)\s*(.*?)\s*$", re.M)


def answer_kind(target: str) -> str:
    """``choice`` | ``binary`` | ``number`` | ``dyck`` | ``text`` from the gold target's form."""
    t = str(target).strip()
    if _CHOICE.fullmatch(t):
        return "choice"
    if any(t.lower() in pair for pair in BINARY_PAIRS):
        return "binary"
    if re.fullmatch(r"-?\d+", t):
        return "number"
    if t and set(t) <= _BRACKETS | {" "}:
        return "dyck"
    return "text"


def parse_options(question: str) -> dict[str, str]:
    """``{"a": normalised option text}`` from ``(A) ...`` lines of a multiple-choice question."""
    return {m.group(1).lower(): normalize(m.group(2)) for m in _OPTION_LINE.finditer(str(question or ""))}


def _choice_letter(ans: str, question: Optional[str]) -> Optional[str]:
    letters = set(re.findall(r"\(([a-z])\)", ans))
    if len(letters) == 1:
        return letters.pop()
    if letters:
        return None                     # several options named: a hedge, never credited
    m = re.fullmatch(r"(?:(?:the\s+)?(?:final\s+)?(?:answer|option|choice)\s*(?:is)?\s*:?\s*)?([a-z])\s*[).:]?", ans)
    if m:
        return m.group(1)
    opts = parse_options(question or "")
    hits = [k for k, v in opts.items() if v and v == ans]
    return hits[0] if len(hits) == 1 else None


def _binary_ok(tgt: str, ans: str) -> bool:
    if ans == tgt:
        return True
    pair = next(p for p in BINARY_PAIRS if tgt in p)
    words = re.findall(r"[a-z']+", ans)
    named = {w for w in words if w in pair}
    return named == {tgt} and len(words) <= 8 and not _NEGATION.search(ans)


def _number_ok(tgt: str, ans: str) -> bool:
    nums = _NUM.findall(ans)
    if nums:
        try:
            return abs(float(nums[-1].replace(",", "")) - float(tgt)) < 1e-9
        except ValueError:
            return False
    words = [w for w in re.findall(r"[a-z]+", ans) if w in _NUM_WORDS]
    return bool(words) and _NUM_WORDS[words[-1]] == int(tgt)


def _dyck_ok(tgt: str, ans: str, question: Optional[str]) -> bool:
    m = re.search(r"[\s()\[\]{}<>]+$", ans)
    got = re.sub(r"\s", "", m.group(0)) if m else ""
    want = re.sub(r"\s", "", tgt)
    if not got:
        return False
    if got == want:
        return True
    q = re.search(r"Input:\s*(.*)$", str(question or ""), re.S)
    prefix = re.sub(r"\s", "", q.group(1)) if q else None
    return bool(prefix) and set(prefix) <= _BRACKETS and got == prefix + want


def _tokens(s: str) -> list[str]:
    return re.sub(r"[\[\]\"',;]", " ", s).split()


_LEAD_IN = re.compile(r"^(?:so\s+)?(?:the\s+)?(?:final\s+)?(?:answer|sorted\s+(?:list|words|order))\s*(?:is\b)?\s*:?\s*")


def _text_ok(tgt: str, ans: str) -> bool:
    want = _tokens(normalize(tgt))
    for cand in dict.fromkeys((ans, _LEAD_IN.sub("", ans, count=1))):
        got = _tokens(cand)
        if got == want:
            return True
        n = len(want)
        if n and len(got) > n and got[-n:] == want and got[-n - 1].endswith(":"):
            return True
    return False


def grade_answer(target: str, answer: str, question: Optional[str] = None) -> bool:
    """Is the extracted ``answer`` text correct for ``target``? (``question`` resolves option texts.)"""
    tgt = normalize(target)
    ans = normalize(answer)
    kind = answer_kind(str(target))
    if kind == "choice":
        letter = _choice_letter(ans, question)
        return letter is not None and letter == tgt.strip("()")
    if kind == "binary":
        return _binary_ok(tgt, ans)
    if kind == "number":
        return _number_ok(tgt, ans)
    if kind == "dyck":
        return _dyck_ok(tgt, ans, question)
    return _text_ok(str(target), ans)


def is_correct(target: str, output: str, question: Optional[str] = None) -> bool:
    """Grade a harness output: extract the final answer, then :func:`grade_answer`."""
    return grade_answer(str(target), extract_answer(str(output)), question)


def is_generic_target(target: str) -> bool:
    """A target too common to be evidence of leakage (option letters, yes/no words, numbers, short sequences)."""
    t = str(target).strip()
    kind = answer_kind(t)
    if kind in ("choice", "binary", "number"):
        return True
    return len(t.split()) < 3 and len(t) < 8
