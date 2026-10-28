"""Static leak check of final harnesses against the evolve examples."""
from __future__ import annotations

from rsi.core import Artifact
from rsi.domains.bbh import BBHDomain, leak_check, make_suite


def _suite(kw):
    return make_suite(["dyck_languages", "word_sorting", "date_understanding"], ["navigate"],
                      per_family={"evolve": 4, "holdout": 2, "ood": 2}, **kw)


def _with(text: str) -> Artifact:
    seed = BBHDomain.seed_artifact()
    return seed.with_files({"prompts/system.md": seed["prompts/system.md"] + "\n" + text})


def test_seed_harness_passes(kw):
    s = _suite(kw)
    r = leak_check(BBHDomain.seed_artifact(), s)
    assert r["passed"] and r["checked"]["tasks"] == 12 and r["checked"]["non_generic_targets"] > 0


def test_a_specific_40_char_substring_of_an_evolve_question_fails(kw):
    s = _suite(kw)
    q = next(t for t in s.split("evolve") if t.family == "date_understanding")
    snippet = q.input[5:45]
    r = leak_check(_with(f"Remember: {snippet}"), s)
    assert not r["passed"] and r["hits"][0]["kind"] == "question" and r["hits"][0]["task"] == q.id
    assert leak_check(_with(f"Remember: {q.input[5:44]}"), s)["passed"]           # 39 chars: below the window


def test_family_boilerplate_shared_by_evolve_questions_is_not_a_leak(kw):
    s = _suite(kw)
    lead = "Complete the rest of the sequence, making sure that the parentheses are closed properly."
    assert sum(lead in t.input for t in s.split("evolve")) >= 2
    assert leak_check(_with(lead), s)["passed"]


def test_a_full_non_generic_evolve_target_fails_but_generic_tokens_do_not(kw):
    s = _suite(kw)
    ws = next(t for t in s.split("evolve") if t.family == "word_sorting")
    r = leak_check(_with(f"Example answer: {ws.target}"), s)
    assert not r["passed"] and any(h["kind"] == "target" and h["task"] == ws.id for h in r["hits"])
    dy = next(t for t in s.split("evolve") if t.family == "dyck_languages")
    assert not leak_check(_with(f"e.g. ANSWER: {dy.target}"), s)["passed"]
    assert leak_check(_with(f"e.g. ( [ {dy.target} ]"), s)["passed"]           # glued to more brackets: not standalone
    assert leak_check(_with("Answer with (A), True, Yes, valid or a number such as 42."), s)["passed"]


def test_holdout_text_is_not_checked(kw):
    s = _suite(kw)
    h = next(t for t in s.split("holdout", allow_sealed=True) if t.family == "date_understanding")
    assert leak_check(_with(h.input), s)["passed"]
