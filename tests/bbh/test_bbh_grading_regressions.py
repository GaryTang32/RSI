"""Answer forms seen in the live pilot that the first grader got wrong."""
from rsi.domains.bbh.grading import grade_answer


def test_binary_leading_word_with_explanation():
    assert grade_answer("No", "No, Fidel does not tell the truth. Fidel lies.")
    assert grade_answer("no", "No, the sentence is not plausible.")
    assert not grade_answer("yes", "No, the sentence is not plausible.")
    assert not grade_answer("Yes", "No, she lies, so yes is wrong")
    assert not grade_answer("No", "No or yes, it depends")


def test_text_target_matched_by_option_letter():
    q = "Which is a humorous edit?\nOptions:\n(A) rita, sue and bob too\n(B) rita, sue and bob poo\n"
    assert grade_answer("rita, sue and bob poo", "(B)", q)
    assert not grade_answer("rita, sue and bob poo", "(A)", q)
    assert grade_answer("rita, sue and bob poo", "rita, sue and bob poo", q)
