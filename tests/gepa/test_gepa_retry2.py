"""GEPA claim-audit retry round 2 (docs/methods/gepa/claims-audit.md, section 6)."""
from pathlib import Path

import pytest

from rsi.gepa import Config

EXPERIMENTS = Path(__file__).resolve().parents[2] / "experiments" / "gepa"


def _merge_check_rollouts(res) -> list[int]:
    """Rollouts spent before each iteration that checked a merge."""
    tr = res.state.trace
    return [tr[k - 1]["rollouts"] if k else 0 for k, e in enumerate(tr) if e.get("invoked_merge")]


def test_merge_start_frac_delays_merge_scheduling(merge_run):
    early = _merge_check_rollouts(merge_run())
    late_res = merge_run(merge_start_frac=0.5)
    late = _merge_check_rollouts(late_res)
    assert early and min(early) < 750, "the reference schedule checks merges early in this run"
    assert late, "merges still happen after the window opens"
    assert min(late) >= 750, "no merge may be checked before 50% of B = 1500 is spent"
    assert all(e["i"] > 0 for e in late_res.state.trace if e.get("merged"))


def test_merge_start_frac_zero_is_the_reference_schedule(merge_run):
    a, b = merge_run(seed=1), merge_run(seed=1, merge_start_frac=0.0)
    assert [e.get("event") for e in a.state.trace] == [e.get("event") for e in b.state.trace]
    assert a.best.files == b.best.files
    assert Config().merge_start_frac == 0.0


@pytest.mark.parametrize("kw", [dict(merge_start_frac=1.0), dict(merge_start_frac=-0.1),
                                dict(merge_start_frac=0.3, max_metric_calls=None)])
def test_merge_start_frac_validation(kw):
    with pytest.raises(ValueError):
        Config(**kw).validate()


def test_merge_check_without_triplet_is_free_and_keeps_merge_due(merge_run):
    """A merge check without a valid triplet charges no rollout, as in the reference (L6 relies on it)."""
    res = merge_run(seed=0)
    tr = res.state.trace
    none_checks = [e for e in tr if e.get("invoked_merge") and not e.get("merged")]
    assert none_checks, "this run has merge checks that found no valid triplet"
    assert all(e.get("event") not in ("merge_accepted", "merge_rejected") for e in none_checks)
    assert res.meta["n_merge_invocations"] == sum(1 for e in tr if e.get("merged"))


def test_paper_regime_settings_build_paper_shaped_worlds(monkeypatch):
    monkeypatch.syspath_prepend(str(EXPERIMENTS))
    import r2_paper_regime as r2
    d = r2.domain("hover", "qwen", 7)
    assert len(d.world.cfg.modules) == 4 and d.world.cfg.scoring == "binary"
    assert len(d.tasks.splits["evolve"]) == 150 and len(d.tasks.splits["val"]) == 300
    assert d.world.cfg.capacity == 8 and d.world.cfg.slip == 0.15
    a = r2.domain("aime", "gpt", 7)
    assert a.world.cfg.modules == ("cot",) and len(a.tasks.splits["val"]) == 45
    assert {k: v["B"] for k, v in r2.SETTINGS.items()} == {"hotpotqa": 6871, "ifbench": 3593, "hover": 7051,
                                                            "pupa": 2426, "aime": 1839, "livebench": 1839}
    ratios = [v["B"] / v["n_val"] for v in r2.SETTINGS.values()]
    assert 11 < min(ratios) and max(ratios) < 42           # the paper's 12-41 rollouts per validation example


def test_r2_paper_regime_results_carry_preregistered_analyses(result):
    o = result("r2_paper_regime.json")
    A = o["analysis"]
    for key in ("L3[qwen]", "L3[gpt]", "L4[qwen]", "L12[qwen]", "L6", "L13[qwen]", "Q10", "Q13", "L2", "Q7", "L10"):
        assert key in A, key
    assert o["config"]["seeds"] == [100, 149] and o["config"]["rl_seeds"] == [100, 129]
    assert set(A["L6"]) >= {"pooled_invocations_per_iteration", "pooled_no_triplet_fraction", "cells_sparse",
                            "verdict"}
    hard = [r for r in o["raw"] if r["arm"] in ("merge_hard5", "merge_hard5_late")]
    assert hard and max(r["n_merge_invocations"] for r in hard) <= 5
    late = [r for r in o["raw"] if r["arm"] == "merge_hard5_late" and r["first_merge_rollouts"] is not None]
    assert all(r["first_merge_rollouts"] >= 0.5 * r["B"] for r in late)


def test_r2_e1_replication_uses_fresh_seeds(result):
    o = result("r2_e1_replication.json")
    assert o["config"]["seeds"] == [20, 59]
    assert {r["seed"] for r in o["raw"]} == set(range(20, 60))
    assert "mde_80pct_power" in o["analysis"]["L2"]


def test_r2_multitask_results(result):
    o = result("r2_multitask.json")
    assert o["config"]["budgets_per_task"] == [10, 20, 50]
    assert all(f"k={k}" in o["analysis"] for k in (10, 20, 50))
    # equal total rollouts: never above 30 k plus the reference soft-budget overshoot
    assert all(r["multi_rollouts"] <= 30 * r["k"] + 2 * 3 + 30 for r in o["raw"])


def test_r2_live_q10_results_are_complete_and_within_spend_cap(result):
    """R2-D: both arms live on fresh seeds, every launched seed reported, spend under the cap."""
    o = result("r2_live_q10.json")
    assert o["partial"] is False and o["setting"] == "ifbench" and o["model"] == "gpt"
    raw = o["raw"]
    live = [r for r in raw if r["proposer"] == "live"]
    seeds = sorted({r["seed"] for r in live})
    assert seeds == list(range(300, 300 + len(seeds))) and len(seeds) >= 2
    for arm in ("gepa", "mipro"):
        assert sorted(r["seed"] for r in live if r["arm"] == arm) == seeds
        assert sorted(r["seed"] for r in raw if r["proposer"] == "mock" and r["arm"] == arm) == list(range(300, 308))
    A = o["analysis"]
    assert A["live_spend_usd"] <= 4.0
    d1 = A["D1_gepa_live_minus_mipro_live"]
    assert d1["n"] == len(seeds)
    assert A["decision"].startswith("mock negative confirmed" if d1["hi"] < 0 else "mock negative not confirmed")
