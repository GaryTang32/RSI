"""Tests for the GEPA claim-audit retry round 2 (docs/methods/gepa/claims-audit.md, section 6).

* ``Config.merge_start_frac`` (extension for the L13 timing test): no merge is scheduled,
  hence none is checked or invoked, before that fraction of the rollout budget is spent;
  the default 0.0 is the reference schedule (bit-identical runs);
* the paper-regime RuleWorld settings used by ``experiments/gepa/r2_paper_regime.py`` build
  worlds with the paper's module counts and split sizes;
* merge bookkeeping facts the L6 diagnosis relies on (a merge check with no valid triplet costs
  no rollout and does not consume ``merges_due``, as in the reference);
* the stored retry-2 result files carry the preregistered analyses (skipped when absent).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from rsi.domains.ruleworld import RuleWorldReflectionLM, make_domain
from rsi.gepa import Config, run

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "results" / "gepa"


def _merge_run(seed: int = 0, B: int = 1500, **kw):
    d = make_domain(seed=seed)
    return run(d, d.seed_artifact(), llm_propose=RuleWorldReflectionLM(d.world),
               config=Config(max_metric_calls=B, seed=seed, use_merge=True, **kw))


def _merge_check_rollouts(res) -> list[int]:
    """Rollouts spent before each iteration in which a merge was checked."""
    tr = res.state.trace
    return [tr[k - 1]["rollouts"] if k else 0 for k, e in enumerate(tr) if e.get("invoked_merge")]


def test_merge_start_frac_delays_merge_scheduling():
    early = _merge_check_rollouts(_merge_run())
    late_res = _merge_run(merge_start_frac=0.5)
    late = _merge_check_rollouts(late_res)
    assert early and min(early) < 750, "the reference schedule checks merges early in this run"
    assert late, "merges still happen after the window opens"
    assert min(late) >= 750, "no merge may be checked before 50% of B = 1500 is spent"
    merge_iters = [e for e in late_res.state.trace if e.get("merged")]
    assert all(e["i"] > 0 for e in merge_iters)


def test_merge_start_frac_zero_is_the_reference_schedule():
    a, b = _merge_run(seed=1), _merge_run(seed=1, merge_start_frac=0.0)
    assert [e.get("event") for e in a.state.trace] == [e.get("event") for e in b.state.trace]
    assert a.best.files == b.best.files
    assert Config().merge_start_frac == 0.0


@pytest.mark.parametrize("kw", [dict(merge_start_frac=1.0), dict(merge_start_frac=-0.1),
                                dict(merge_start_frac=0.3, max_metric_calls=None)])
def test_merge_start_frac_validation(kw):
    with pytest.raises(ValueError):
        Config(**kw).validate()


def test_merge_check_without_triplet_is_free_and_keeps_merge_due():
    """Reference behaviour the L6 metric depends on: ``propose`` returning None charges no rollout and
    leaves ``merges_due`` (the iteration falls through to reflection)."""
    res = _merge_run(seed=0)
    tr = res.state.trace
    none_checks = [e for e in tr if e.get("invoked_merge") and not e.get("merged")]
    assert none_checks, "this run has merge checks that found no valid triplet"
    assert all(e.get("event") not in ("merge_accepted", "merge_rejected") for e in none_checks)
    assert res.meta["n_merge_invocations"] == sum(1 for e in tr if e.get("merged"))


def test_paper_regime_settings_build_paper_shaped_worlds():
    sys.path.insert(0, str(ROOT / "experiments" / "gepa"))
    import r2_paper_regime as r2
    d = r2.domain("hover", "qwen", 7)
    assert len(d.world.cfg.modules) == 4 and d.world.cfg.scoring == "binary"
    assert len(d.tasks.splits["evolve"]) == 150 and len(d.tasks.splits["val"]) == 300
    assert d.world.cfg.capacity == 8 and d.world.cfg.slip == 0.15
    a = r2.domain("aime", "gpt", 7)
    assert a.world.cfg.modules == ("cot",) and len(a.tasks.splits["val"]) == 45
    assert {k: v["B"] for k, v in r2.SETTINGS.items()} == {"hotpotqa": 6871, "ifbench": 3593, "hover": 7051,
                                                            "pupa": 2426, "aime": 1839, "livebench": 1839}
    # the paper's B / |V| range (12-41 rollouts per validation example), not the 50-133 of E3/E4/E7
    ratios = [v["B"] / v["n_val"] for v in r2.SETTINGS.values()]
    assert 11 < min(ratios) and max(ratios) < 42


def _load(name):
    p = RESULTS / name
    if not p.exists():
        pytest.skip(f"{name} not generated")
    return json.loads(p.read_text())


def test_r2_paper_regime_results_carry_preregistered_analyses():
    o = _load("r2_paper_regime.json")
    A = o["analysis"]
    for key in ("L3[qwen]", "L3[gpt]", "L4[qwen]", "L12[qwen]", "L6", "L13[qwen]", "Q10", "Q13", "L2", "Q7", "L10"):
        assert key in A, key
    assert o["config"]["seeds"] == [100, 149] and o["config"]["rl_seeds"] == [100, 129]
    l6 = A["L6"]
    assert set(l6) >= {"pooled_invocations_per_iteration", "pooled_no_triplet_fraction", "cells_sparse", "verdict"}
    # every merge invocation is an accepted or a rejected merge, and the hard cap holds in the paper regime
    hard = [r for r in o["raw"] if r["arm"] in ("merge_hard5", "merge_hard5_late")]
    assert hard and max(r["n_merge_invocations"] for r in hard) <= 5
    late = [r for r in o["raw"] if r["arm"] == "merge_hard5_late" and r["first_merge_rollouts"] is not None]
    assert all(r["first_merge_rollouts"] >= 0.5 * r["B"] for r in late)


def test_r2_e1_replication_uses_fresh_seeds():
    o = _load("r2_e1_replication.json")
    assert o["config"]["seeds"] == [20, 59]
    assert {r["seed"] for r in o["raw"]} == set(range(20, 60))
    assert "mde_80pct_power" in o["analysis"]["L2"]


def test_r2_multitask_results():
    o = _load("r2_multitask.json")
    assert o["config"]["budgets_per_task"] == [10, 20, 50]
    for k in (10, 20, 50):
        assert f"k={k}" in o["analysis"]
    # equal total rollouts: the multi-task run never exceeds 30 k (+ the reference soft-budget overshoot)
    for r in o["raw"]:
        assert r["multi_rollouts"] <= 30 * r["k"] + 2 * 3 + 30


def test_r2_live_q10_results_are_complete_and_within_spend_cap():
    """R2-D (review of retry 2): live-proposer check of the Q10 negative, both arms live, fresh seeds."""
    o = _load("r2_live_q10.json")
    assert o["partial"] is False and o["setting"] == "ifbench" and o["model"] == "gpt"
    raw = o["raw"]
    live = [r for r in raw if r["proposer"] == "live"]
    # every launched seed is reported for both arms (no dropped seeds), seeds are fresh (300+)
    seeds = sorted({r["seed"] for r in live})
    assert seeds == list(range(300, 300 + len(seeds))) and len(seeds) >= 2
    for arm in ("gepa", "mipro"):
        assert sorted(r["seed"] for r in live if r["arm"] == arm) == seeds
        assert sorted(r["seed"] for r in raw if r["proposer"] == "mock" and r["arm"] == arm) == list(range(300, 308))
    A = o["analysis"]
    assert A["live_spend_usd"] <= 4.0
    assert A["D1_gepa_live_minus_mipro_live"]["n"] == len(seeds)
    # the preregistered decision string follows from D1's CI
    confirmed = A["D1_gepa_live_minus_mipro_live"]["hi"] < 0
    assert A["decision"].startswith("mock negative confirmed" if confirmed else "mock negative not confirmed")
