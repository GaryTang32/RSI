"""The seed prompt's placeholder ``ANSWER: <answer>`` makes the model wrap answers in angle brackets (live pilot)."""
from rsi.domains.bbeh.grading import is_correct


def test_angle_wrapped_answer_line_is_unwrapped():
    assert is_correct("yes, no, no", "reasoning...\nANSWER: <yes, no, no>")
    assert is_correct("yes, no, no", "ANSWER: **<yes, no, no>**")
    assert not is_correct("no, yes, no", "ANSWER: <yes, yes, no>")
    assert is_correct("14", "ANSWER: 14")
