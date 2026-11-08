"""The leak tripwire tells a quotation of the task's own text (even cut off) from a grader verdict."""

import pytest

from rsi.harness2.guard import GraderLeak, check_text

MARKERS = ('"pass_rate"',)
TASK = 'Summarise this review data: {"score": 4, "review": "great value, would buy again", "id": 17}. ' * 3


def test_a_truncated_quotation_of_the_task_is_not_a_leak():
    trace = "agent prompt (first 60 chars): " + TASK[:60] + " [truncated]"
    check_text(trace, MARKERS, "trace", own=[TASK])  # no exact copy of TASK, but the marker sits in a quoted stretch


def test_a_grader_score_beside_the_task_text_is_still_a_leak():
    trace = TASK + '\nGRADER: {"score": 0.0, "feedback": "wrong"}'
    with pytest.raises(GraderLeak):
        check_text(trace, MARKERS, "trace", own=[TASK])


def test_a_grader_score_without_any_task_text_is_a_leak():
    with pytest.raises(GraderLeak):
        check_text('result: {"score": 1.0}', MARKERS, "trace", own=[TASK])
    with pytest.raises(GraderLeak):
        check_text('the run had "pass_rate": 0.9', MARKERS, "trace", own=[TASK])


def test_a_short_coincidental_overlap_does_not_excuse_a_marker():
    with pytest.raises(GraderLeak):  # only a few characters around the marker match the task's text
        check_text('grader {"score": 1, "y": 2}', MARKERS, "trace", own=['data {"score": 1, "x": 0}'])
