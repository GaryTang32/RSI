"""BBEH grading: the official evaluate.py examples, our ANSWER: extraction in front, and the gold-target round trip."""
from __future__ import annotations

import pytest

from rsi.domains.bbeh import TASKS, BBHDataError, evaluate_correctness, extract_answer, is_correct, is_generic_target
from rsi.domains.bbeh import load_examples


@pytest.mark.parametrize("sample, ref, ok", [         # the examples printed by bbeh/evaluate.py
    ("Ok The final answer is: \\boxed{4}.", "4", True), ("[Reasoning] The final answer is: \\boxed{4}.", "3", False),
    ("Alright! The final answer is: 2, 3, 4", "2,3,4", True), ("blah blah The final answer is: 2, 3, 4", "2,3,5", False),
    ("Ok The answer is: (A)", "a", True), ("Ok The answer is: (A)", "b", False),
    ("Ok The answer is: **25**\nHere's why.", "25.0", True), ("Ok The answer is: **25**\nHere's why.", "26.0", False),
])
def test_official_examples(sample, ref, ok):
    assert evaluate_correctness(sample, ref) is ok and is_correct(ref, sample) is ok


@pytest.mark.parametrize("target, output, ok", [
    ("(E)", "work...\nANSWER: (E)", True), ("(E)", "ANSWER: E", True), ("(E)", "ANSWER: (e).", True),
    ("(E)", "ANSWER: (D)", False), ("(E)", "**ANSWER:** **(E)**", True),
    ("Yes", "ANSWER: yes", True), ("Yes", "ANSWER: No", False),
    ("-52", "ANSWER: -52.0", True), ("-52", "ANSWER: $-52$", True), ("-52", "ANSWER: \\boxed{-52}", True),
    ("-52", "ANSWER: 52", False),
    ("no, yes, no", "ANSWER: no,yes,no", True), ("no, yes, no", "ANSWER: No, Yes, No.", True),
    ("no, yes, no", "ANSWER: no, no, no", False),
    ("[emma, lily]", "ANSWER: [emma, lily]", True), ("emma, lily", "ANSWER: [emma, lily]", True),
    ("disproved", "ANSWER: 'disproved'", True), ("disproved", "ANSWER: disproved?", True),
    ("He really cried.", "ANSWER: He really cried.", True), ("He really cried.", "ANSWER: He really cried", True),
    ("He really cried.", "ANSWER: He cried.", False),
    ("7", "The answer is 6. Wait, recount.\nANSWER: 7", True),            # the ANSWER line wins
    ("7", "Reasoning...\nThe final answer is: 7.", True),                 # no ANSWER line: official prefixes
    ("7", "7\nbecause...", True), ("7", "because...\n7", False),          # official: first line of the text
    ("150, 2", "ANSWER: The final answer is: 150, 2.", True),
])
def test_extraction_and_fuzzy_match(target, output, ok):
    assert is_correct(target, output) is ok


def test_feedback_answer_and_generic_rule():
    assert extract_answer("x\nANSWER: **(B)**.") == "(B)" and extract_answer("The final answer is: 42.") == "42"
    assert all(is_generic_target(t) for t in ("(A)", "Yes", "-52", "150, 2", "[emma, lily]", "els llaços"))
    assert not any(is_generic_target(t) for t in ("no, yes, no", "A, A, A", "justifications, pristine, pvc"))


def _round_trip(examples) -> list:
    return [(e["target"], out) for e in examples for out in (f"ANSWER: {e['target']}",
                                                             f"The final answer is: {e['target']}.")
            if not is_correct(e["target"], out)]


def test_every_gold_target_round_trips_on_the_synthetic_files(bkw):
    bad = {t: _round_trip(load_examples(t, **bkw)) for t in TASKS}
    assert not any(bad.values()), {t: b[:3] for t, b in bad.items() if b}


def test_every_gold_target_round_trips_on_the_real_files():
    """All 23 real files when already cached and verified (no download in tests); else skipped."""
    try:
        data = {t: load_examples(t, download=False) for t in TASKS}
    except BBHDataError as e:
        pytest.skip(f"real BBEH files not cached: {e}")
    assert sum(len(v) for v in data.values()) == 4520
    bad = {t: _round_trip(v) for t, v in data.items()}
    assert not any(bad.values()), {t: b[:3] for t, b in bad.items() if b}
