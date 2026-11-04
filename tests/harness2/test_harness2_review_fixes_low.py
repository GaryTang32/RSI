"""Harness² low-severity review fix (L9): ``best_of_n`` grades the streamed tasks it was given."""

from __future__ import annotations

import dataclasses

from harness2_helpers import RecordingImprover

from rsi.harness2 import Config, best_of_n
from rsi.harness2.api import stream_tasks


def test_best_of_n_report_grades_custom_streamed_tasks(dom, sim):
    """L9: a streamed task whose id is not in ``domain.tasks`` used to raise KeyError after every call was spent."""
    (orig,) = stream_tasks(dom, Config(split="holdout", max_tasks=1))
    custom = dataclasses.replace(orig, id=orig.id + "-custom")
    assert custom.id not in dom.tasks.tasks
    out = best_of_n(dom, dom.seed_artifact(), llm_task=sim, llm_select=RecordingImprover(), n=2, tasks=[custom])
    assert out["report"]["n"] == 1 and custom.id in out["report"]["per_task"]


def test_best_of_n_report_uses_the_streamed_copy_not_the_suite_copy(dom, sim):
    """L9: the streamed task (here: a different family label) is the one graded, as ``run`` does."""
    (orig,) = stream_tasks(dom, Config(split="holdout", max_tasks=1))
    relabelled = dataclasses.replace(orig, family="streamed-family")
    out = best_of_n(dom, dom.seed_artifact(), llm_task=sim, llm_select=RecordingImprover(), n=2, tasks=[relabelled])
    assert out["report"]["per_task"][orig.id]["family"] == "streamed-family"
