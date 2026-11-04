"""Autoresearch low-severity review fixes (L6): rewinding to a kept commit after a validation resample."""

from test_autoresearch_fixes import ListAgent, ToyTask, cfg, set_x

from rsi.autoresearch import AutoresearchLoop
from rsi.core.editors import Proposal


def _rewind_to_baseline(ctx):
    return Proposal(None, error="no file blocks", meta={"rewind": ctx.git_log.splitlines()[-1][:7]})


def test_rewind_to_pre_resample_keep_rescores_instead_of_crashing():
    """L6: keep, keep, val resample, then ``REWIND <baseline>`` used to raise a bare StopIteration out of run()."""
    loop = AutoresearchLoop(
        ToyTask(), ListAgent([set_x(4), set_x(3), _rewind_to_baseline]), cfg(max_experiments=3, val_resample_every=2)
    )
    loop.run()
    assert loop.counters["rewinds"] == 1
    assert loop.inc["artifact"]["train.py"] == "X = 5\n"
    assert loop.inc["samples"].mean == 5.0
    # the rewound-to incumbent was re-scored on the current validation epoch and joins this epoch's keeps
    base = loop.inc["node"]
    assert any(nid == base for nid, _ in loop.keeps)
    assert f"rescore_epoch_{loop.val_epoch}" in loop.ledger[base].meta


def test_rewind_to_unknown_keep_samples_raises_key_error():
    """L6: a kept ledger node with no recorded samples is a KeyError (handled as an invalid proposal), not StopIteration."""
    loop = AutoresearchLoop(ToyTask(), ListAgent([set_x(4)]), cfg(max_experiments=1))
    loop.run()
    base = loop.ledger.nodes()[0]
    loop.keep_samples.pop(base.id)
    try:
        loop.rewind(str(base.meta["commit"]))
    except KeyError:
        pass
    else:
        raise AssertionError("expected KeyError")


def test_rigor_keep_rule_warns_when_one_run_per_arm_cannot_be_bootstrapped():
    import pytest

    from rsi.autoresearch import BootstrapRigorKeep

    with pytest.warns(UserWarning, match="repeats >= 2"):
        BootstrapRigorKeep(repeats=1)
