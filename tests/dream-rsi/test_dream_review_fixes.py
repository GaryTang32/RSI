"""Dream-RSI review fixes: rejected revisions under the guarded selector (M11), string ``llm_develop`` (M12)."""

import numpy as np
import pytest

import rsi
from rsi.core import MockLLM, get_llm
from rsi.domains.discovery import SyntheticConfig, SyntheticDomain
from rsi.dream import Config, GuardedSelector, LLMPolicyDeveloper, PolicyReport, run
from rsi.dream.developer import Revision


def small_cfg(**kw) -> Config:
    return Config(
        **{"rounds": 3, "W": 3, "branch_count": 4, "refine_count": 3, "M": 3, "sandbox": "inprocess", "seed": 3, **kw}
    )


def _rep(vals: list[float]) -> PolicyReport:
    return PolicyReport("p", "", "eq1", float(np.mean(vals)) if vals else float("-inf"), list(vals), [])


def test_guarded_selector_scores_a_rejected_revision_minus_inf():
    """M11: a rejected revision (``per_world=[]``) is never selected and never raises IndexError."""
    inc, good, bad = _rep([0.5, 0.5, 0.5]), _rep([0.9, 0.9, 0.9]), _rep([])
    sel = GuardedSelector(margin_floor=0.0).select([inc, bad, good])
    assert sel.values[1] == float("-inf")
    assert sel.index == 2
    sel = GuardedSelector().select([inc, bad, bad])
    assert sel.index == 0 and "keep incumbent" in sel.reason


class _FailingDeveloper(LLMPolicyDeveloper):
    """An LLM developer whose every revision failed repair (``Revision(None)``)."""

    def revise(self, ctx, seed=0):
        return Revision(None, error="repair failed")


def test_guarded_run_survives_failed_llm_revisions():
    """M11: ``selector="guarded"`` + a developer returning ``Revision(None)`` keeps the incumbent, run completes."""
    res = run(
        SyntheticDomain(SyntheticConfig(seed=2)),
        config=small_cfg(rounds=2, selector="guarded"),
        developer=_FailingDeveloper(MockLLM()),
    )
    rows = [r["dream"] for r in res.trajectory if "dream" in r]
    assert rows
    for row in rows:
        assert row["selected"] == 0
        assert all(v == float("-inf") for v in row["values"][1:])


def test_string_llm_develop_is_resolved_and_cached(tmp_path, monkeypatch):
    """M12: ``improve(method="dream", llm_develop="mock")`` resolves the spec (with ``cache_dir``), not AttributeError."""
    seen = {}
    real = rsi.api.get_llm

    def spy(spec, **kw):
        seen.setdefault(spec, []).append(kw.get("cache_dir"))
        return real(spec, **kw)

    monkeypatch.setattr(rsi.api, "get_llm", spy)
    res = rsi.improve(
        SyntheticDomain(SyntheticConfig(seed=2)),
        method="dream",
        llm_develop="mock",
        config={"rounds": 2, "W": 3, "branch_count": 4, "refine_count": 3, "M": 3, "sandbox": "inprocess"},
        cache_dir=str(tmp_path / "cache"),
    )
    assert any("dream" in r for r in res.trajectory)
    assert seen.get("mock") == [str(tmp_path / "cache")]


def test_string_llm_develop_direct_run():
    """M12: a direct ``rsi.dream.run(llm_develop="mock")`` also resolves the spec string."""
    res = run(SyntheticDomain(SyntheticConfig(seed=2)), config=small_cfg(rounds=2), llm_develop="mock")
    assert any("dream" in r for r in res.trajectory)
    assert res.usage["_cost"]["developer_calls"] >= 1


def test_get_llm_still_rejects_unknown_specs():
    with pytest.raises(ValueError):
        get_llm("nope:x")
