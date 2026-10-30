"""BBH grading: every answer format, the round trip of every gold target, and the feedback convention."""
from __future__ import annotations

import pytest

from rsi.domains.bbh import TASKS, BBHDataError, answer_kind, grade_answer, is_correct, is_generic_target, load_examples

Q_MC = "Which is it?\nOptions:\n(A) 07/09/1972\n(B) 12/01/1972\n(C) circle\n(D) & fire\n(E) & fire"
Q_DYCK = "Complete the rest of the sequence, making sure that the parentheses are closed properly. Input: < [ ] { ( )"


@pytest.mark.parametrize("target, output, ok", [
    # multiple choice
    ("(A)", "ANSWER: (A)", True), ("(A)", "ANSWER: A", True), ("(A)", "ANSWER: a.", True), ("(A)", "ANSWER: A)", True),
    ("(A)", "**ANSWER:** (A) 07/09/1972", True), ("(A)", "ANSWER: option A", True), ("(A)", "The answer is (A).", True),
    ("(A)", "ANSWER: 07/09/1972", True),                    # unambiguous option text
    ("(C)", "ANSWER: Circle", True),
    ("(D)", "ANSWER: & fire", False),                       # option text shared by (D) and (E): ambiguous
    ("(A)", "ANSWER: (A) or (B)", False), ("(A)", "ANSWER: (B)", False), ("(A)", "ANSWER: B", False),
    ("(A)", "ANSWER: 12/01/1972", False), ("(A)", "ANSWER: none of them", False),
    # binary
    ("True", "ANSWER: True", True), ("True", "ANSWER: true.", True), ("False", "So the answer is False.", True),
    ("True", "ANSWER: False", False), ("True", "ANSWER: True or False", False), ("True", "ANSWER: not True", False),
    ("Yes", "ANSWER: yes", True), ("No", "ANSWER: **No**", True), ("Yes", "ANSWER: No", False),
    ("yes", "ANSWER: Yes", True), ("no", "ANSWER: yes", False),
    ("valid", "ANSWER: valid", True), ("invalid", "ANSWER: Invalid.", True), ("valid", "ANSWER: invalid", False),
    ("invalid", "ANSWER: valid", False), ("valid", "ANSWER: not valid", False),
    # numbers
    ("-11520", "ANSWER: -11520", True), ("-11520", "ANSWER: −11,520", True), ("12", "ANSWER: 12.0", True),
    ("7", "There are 7 items.\nANSWER: 7", True), ("7", "ANSWER: seven", True), ("7", "ANSWER: 17", False),
    ("-3", "ANSWER: 3", False), ("0", "ANSWER: zero", True),
    # Dyck closing sequences
    ("] )", "ANSWER: ] )", True), ("] )", "ANSWER: ])", True), ("] )", "ANSWER: `] )`", True),
    ("] )", "The closing sequence is ] )", True), ("] )", "ANSWER: ) ]", False), ("] )", "ANSWER: ] ) )", False),
    ("} >", "ANSWER: < [ ] { ( ) } >", True),               # the full sequence: input + target
    ("} >", "ANSWER: < [ ] { } >", False),
    # word lists
    ("apple banana cherry", "ANSWER: apple banana cherry", True),
    ("apple banana cherry", "ANSWER: apple, banana, cherry", True),
    ("apple banana cherry", "ANSWER: [\"apple\", \"banana\", \"cherry\"]", True),
    ("apple banana cherry", "ANSWER:   Apple  banana\tcherry.", True),
    ("apple banana cherry", "Sorted list: apple banana cherry", True),
    ("apple banana cherry", "The answer is apple banana cherry.", True),
    ("apple banana cherry", "ANSWER: banana apple cherry", False),
    ("apple banana cherry", "ANSWER: zebra apple banana cherry", False),   # no suffix credit
    ("apple banana cherry", "ANSWER: apple banana", False),
    # free-text targets (a few BBH rows)
    ("Monsters, Inc", "ANSWER: Monsters, Inc", True), ("Monsters, Inc", "ANSWER: (A)", False),
])
def test_grading_formats(target, output, ok):
    q = Q_DYCK if answer_kind(target) == "dyck" else Q_MC
    assert is_correct(target, output, q) is ok


def test_answer_kinds_and_generic_targets():
    assert [answer_kind(t) for t in ("(B)", "True", "no", "valid", "-4", "] ) }", "a b c", "Monsters, Inc")] == \
        ["choice", "binary", "binary", "binary", "number", "dyck", "text", "text"]
    assert all(is_generic_target(t) for t in ("(B)", "True", "No", "invalid", "-11520", ")", "] )", "ab cd"))
    assert not any(is_generic_target(t) for t in ("] ) }", "apple banana cherry", "dearth, wind, & fire"))


def _round_trip(examples) -> list:
    flips = {"true": "False", "false": "True", "yes": "No", "no": "Yes", "valid": "invalid", "invalid": "valid"}
    bad = []
    for e in examples:
        t, q = e["target"], e["input"]
        for out in (f"ANSWER: {t}", t, f"work\n**ANSWER:** {t}.", f"So the answer is {t}."):
            if not is_correct(t, out, q):
                bad.append(("pos", t, out))
        kind = answer_kind(t)
        neg = {"choice": "(B)" if t == "(A)" else "(A)", "number": str(int(t) + 1) if kind == "number" else "",
               "dyck": t + " )", "binary": flips.get(t.lower(), ""),
               "text": " ".join(reversed(t.split())) if len(set(t.split())) > 1 else t + " extra"}[kind]
        if is_correct(t, f"ANSWER: {neg}", q):
            bad.append(("neg", t, neg))
    return bad


def test_every_gold_target_round_trips_on_the_synthetic_files(kw):
    bad = {t: _round_trip(load_examples(t, **kw)) for t in TASKS}
    assert not any(bad.values()), {t: b[:3] for t, b in bad.items() if b}


def test_every_gold_target_round_trips_on_the_real_files():
    """All 27 real files when they are already cached and verified (no download in tests); else skipped."""
    try:
        data = {t: load_examples(t, download=False) for t in TASKS}
    except BBHDataError as e:
        pytest.skip(f"real BBH files not cached: {e}")
    assert sum(len(v) for v in data.values()) == 6511
    bad = {t: _round_trip(v) for t, v in data.items()}
    assert not any(bad.values()), {t: b[:3] for t, b in bad.items() if b}


def test_grade_answer_is_the_post_extraction_rule():
    assert grade_answer("(A)", "(a)") and not grade_answer("(A)", "ANSWER: (B) because (A) is wrong")
