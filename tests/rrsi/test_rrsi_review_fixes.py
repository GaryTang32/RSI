"""RRSI review fixes: ``reevaluate`` never loses a valid stored measurement (M14)."""

import pytest
from rrsi_helpers import hw_loop, read_json

from rsi.rrsi import drive


def _evals(out, t):
    return {p.parent.name: p.read_text() for p in sorted((out / f"r{t}").glob("*/eval.json"))}


def _round(tmp_path, t=2):
    out = tmp_path / "r"
    _, r = hw_loop(out, T=4)
    drive(r)
    return out, r


def test_reevaluate_refuses_before_measuring_when_later_rounds_exist(tmp_path):
    """M14(b): "truncate first" is raised before any eval is deleted, re-measured or paid for."""
    t = 2
    out, r = _round(tmp_path, t)
    before, rollouts = _evals(out, t), r.measurer.n_rollouts
    assert before
    with pytest.raises(RuntimeError, match="truncate"):
        r.reevaluate(t)
    assert r.measurer.n_rollouts == rollouts
    assert _evals(out, t) == before


def test_reevaluate_keeps_the_stored_eval_when_the_remeasure_is_invalid(tmp_path):
    """M14(a): an invalid (infrastructure) re-measurement leaves the original valid eval and the verdict intact."""
    t = 2
    out, r = _round(tmp_path, t)
    r.truncate(t)
    before = _evals(out, t)
    keys = ("variant", "admissible", "S", "C")
    decisions = [tuple(d[k] for k in keys) for d in read_json(out / f"r{t}" / "decisions.json")]
    r._measure_valid = lambda art, job: (None, "10/10 trials missing (infrastructure) after a retry")
    after = r.reevaluate(t)
    assert _evals(out, t) == before
    assert all("eval_invalid" not in d.reason for d in after if d.variant in before)
    assert [tuple(d.to_json()[k] for k in keys) for d in after] == decisions


def test_reevaluate_replaces_the_eval_with_a_valid_remeasure(tmp_path):
    t = 2
    out, r = _round(tmp_path, t)
    r.truncate(t)
    before = _evals(out, t)
    r.reevaluate(t)
    after = _evals(out, t)
    assert set(after) == set(before)
    assert all(f'"r{t}{v}_re"' in after[v] for v in after)
    assert not list((out / f"r{t}").glob("*/eval.json.tmp")) and not list((out / f"r{t}").glob("*/eval.tmp"))
