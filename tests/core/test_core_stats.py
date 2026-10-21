"""rsi.core.stats: noise band, bootstrap CIs, paired diffs, P(better), Beta LCB, Spearman, Gini."""
from __future__ import annotations

import math
import sys

import numpy as np
import pytest
from scipy.stats import beta, spearmanr

from rsi.core import (NoiseEstimate, beta_lcb, bootstrap_ci, fixed_noise, gini, noise_from_repeats, noise_from_trials,
                      paired_diff_ci, prob_better_bootstrap, spearman, summarize_runs)
from rsi.core.stats import _rank, mean_se


def _all_nan(values) -> bool:
    return all(math.isnan(v) for v in values)


def test_noise_from_repeats_uses_sqrt2_factor():
    ne = noise_from_repeats([0.5, 0.6, 0.7])
    sd = np.std([0.5, 0.6, 0.7], ddof=1)
    assert ne.sd_null == pytest.approx(sd * math.sqrt(2))
    assert ne.delta == pytest.approx(2.0 * sd * math.sqrt(2))
    assert (ne.mode, ne.n, ne.z) == ("repeat", 3, 2.0)
    assert noise_from_repeats([0.5, 0.6, 0.7], z=3).delta == pytest.approx(3 * sd * math.sqrt(2))
    assert noise_from_repeats([0.4, 0.4]).delta == 0.0
    with pytest.raises(ValueError):
        noise_from_repeats([0.5])


def test_noise_from_trials_matches_analytic_within_task_se():
    n = 50
    ne = noise_from_trials(np.tile([0.0, 1.0], (n, 1)), reps=4000, seed=1)
    se = math.sqrt(0.125 / n)                  # resampling trials [0, 1]: var 0.25/2 per task
    assert ne.sd_null == pytest.approx(math.sqrt(2) * se, rel=0.08)
    assert ne.delta == pytest.approx(2 * math.sqrt(2) * se, rel=0.08)
    assert (ne.mode, ne.n) == ("bootstrap", n * 2)


def test_noise_from_trials_resamples_within_tasks_only():
    m = np.array([[0.0, 0.0, 0.0], [1.0, 1.0, 1.0], [0.0, 0.0, 0.0], [1.0, 1.0, 1.0]])
    assert noise_from_trials(m).delta == 0.0   # tasks differ, but each is perfectly repeatable


def test_noise_from_trials_deterministic_and_validates():
    m = np.random.default_rng(0).random((10, 3))
    assert noise_from_trials(m, seed=5).delta == noise_from_trials(m, seed=5).delta
    for bad in (np.ones((5, 1)), np.ones(5)):
        with pytest.raises(ValueError):
            noise_from_trials(bad)


def test_fixed_noise_and_to_json():
    ne = fixed_noise(0.1)
    assert (ne.delta, ne.sd_null, ne.z, ne.mode, ne.n) == (0.1, 0.05, 2.0, "fixed", 0)
    d = ne.to_json()
    assert d == {"delta": 0.1, "sd_null": 0.05, "z": 2.0, "mode": "fixed", "n": 0}
    d["delta"] = 9
    assert ne.delta == 0.1 and isinstance(ne, NoiseEstimate)


def test_bootstrap_ci_properties():
    x = np.random.default_rng(3).normal(10, 2, size=200)
    p, lo, hi = bootstrap_ci(x)
    assert p == pytest.approx(x.mean()) and lo < p < hi
    assert hi - lo == pytest.approx(2 * 1.96 * x.std(ddof=1) / math.sqrt(len(x)), rel=0.15)
    assert bootstrap_ci(x, seed=1) == bootstrap_ci(x, seed=1)
    p90 = bootstrap_ci(x, alpha=0.1)
    assert p90[2] - p90[1] < hi - lo
    assert bootstrap_ci(x, stat=np.median)[0] == pytest.approx(np.median(x))
    assert bootstrap_ci([4.0]) == (4.0, 4.0, 4.0)
    assert _all_nan(bootstrap_ci([]))


def test_paired_diff_ci():
    a = [0.1, 0.5, 0.3, 0.9]
    r = paired_diff_ci(a, [v + 0.2 for v in a])
    assert r["mean_diff"] == pytest.approx(0.2) and r["lo"] == pytest.approx(0.2) and r["hi"] == pytest.approx(0.2)
    assert r["n"] == 4 and r["p_better"] == 1.0
    r = paired_diff_ci([1, 0, 1, 0], [0, 1, 1, 0])
    assert r["mean_diff"] == 0.0 and r["p_better"] == 0.25 and r["lo"] <= 0 <= r["hi"]
    assert math.isnan(paired_diff_ci([], [])["p_better"])


def test_prob_better_bootstrap():
    hi, lo = [0.9, 0.91, 0.92], [0.5, 0.51, 0.52]
    assert prob_better_bootstrap(hi, lo) == 1.0 and prob_better_bootstrap(lo, hi) == 0.0
    assert prob_better_bootstrap(lo, hi, lower_is_better=True) == 1.0
    assert 0.2 < prob_better_bootstrap([0.5, 0.6, 0.7], [0.45, 0.6, 0.72]) < 0.8
    assert prob_better_bootstrap([1, 2, 3], [1, 2, 3], seed=9) == prob_better_bootstrap([1, 2, 3], [1, 2, 3], seed=9)


def test_beta_lcb_matches_scipy_and_is_monotone():
    assert beta_lcb(8, 2) == pytest.approx(beta.ppf(0.05, 9, 3))
    assert beta_lcb(8, 2, q=0.1, prior=(2, 2)) == pytest.approx(beta.ppf(0.1, 10, 4))
    vals = [beta_lcb(s, 10 - s) for s in range(11)]
    assert vals == sorted(vals) and 0 < vals[0] < vals[-1] < 1
    assert beta_lcb(80, 20) > beta_lcb(8, 2)    # more evidence at the same rate: a tighter bound


def test_beta_lcb_normal_fallback_without_scipy(monkeypatch):
    exact = beta_lcb(60, 40)
    monkeypatch.setitem(sys.modules, "scipy.stats", None)   # makes the scipy import fail
    assert beta_lcb(60, 40) == pytest.approx(exact, abs=0.01)
    assert beta_lcb(0, 50) >= 0.0


def test_spearman():
    x = [1, 2, 3, 4, 5]
    assert spearman(x, [v ** 3 for v in x]) == pytest.approx(1.0)
    assert spearman(x, [-v for v in x]) == pytest.approx(-1.0)
    a, b = [1, 2, 2, 3, 5, 5, 7], [3, 1, 2, 2, 9, 4, 8]
    assert spearman(a, b) == pytest.approx(spearmanr(a, b).statistic)
    assert math.isnan(spearman([1, 1, 1], [1, 2, 3])) and math.isnan(spearman([1], [2]))


def test_rank_averages_ties():
    np.testing.assert_allclose(_rank(np.array([10.0, 20.0, 10.0, 30.0])), [0.5, 2.0, 0.5, 3.0])


def test_gini():
    assert gini([5, 5, 5, 5]) == pytest.approx(0.0)
    assert gini([0, 0, 0, 10]) == pytest.approx(0.75)        # (n-1)/n for one holder of everything
    assert gini([3, 1, 2]) == pytest.approx(gini([1, 2, 3]))
    assert gini([]) == 0.0 and gini([0, 0]) == 0.0
    v = np.array([1.0, 2.0, 3.0, 10.0])
    mean_abs_diff = np.abs(v[:, None] - v[None, :]).mean()
    assert gini(v) == pytest.approx(mean_abs_diff / (2 * v.mean()))


def test_mean_se_and_summarize_runs():
    m, se = mean_se([1, 2, 3])
    assert m == 2.0 and se == pytest.approx(1 / math.sqrt(3))
    assert mean_se([4]) == (4.0, 0.0) and _all_nan(mean_se([]))
    s = summarize_runs([0.2, 0.4, 0.6])
    assert set(s) == {"mean", "lo", "hi", "n"} and s["n"] == 3
    assert s["mean"] == pytest.approx(0.4) and s["lo"] <= 0.4 <= s["hi"]
