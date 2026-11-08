"""Statistics for self-improvement loops: noise bands, bootstrap CIs, paired tests, Beta LCBs, rank correlation."""

from __future__ import annotations

import math
import warnings
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np


@dataclass
class NoiseEstimate:
    delta: float  # z * sd(null dS)
    sd_null: float  # sd of the difference between two evaluations of the same artifact
    z: float
    mode: str  # "repeat" | "bootstrap" | "fixed"
    n: int

    def to_json(self) -> dict:
        return self.__dict__.copy()


# Student-t quantiles with the coverage of z = 2 (one-sided 97.72%), by degrees of freedom R - 1.
_T_Z2 = {
    1: 13.968, 2: 4.527, 3: 3.307, 4: 2.869, 5: 2.649, 6: 2.517, 7: 2.429, 8: 2.366, 9: 2.320, 10: 2.284,
    11: 2.255, 12: 2.231, 13: 2.212, 14: 2.195, 15: 2.181, 16: 2.169, 17: 2.158, 18: 2.149, 19: 2.140,
    20: 2.133, 21: 2.126, 22: 2.120, 23: 2.115, 24: 2.110, 25: 2.105, 26: 2.101, 27: 2.097, 28: 2.093, 29: 2.090,
}  # fmt: skip
MIN_RELIABLE_REPEATS = 5


def noise_from_repeats(scores: Sequence[float], z: float = 2.0, small_sample: bool = False) -> NoiseEstimate:
    """delta from R >= 2 repeated evaluations of the unchanged artifact: sd(null dS) = stdev(S_r) * sqrt(2).

    The standard deviation of a few repeats is itself noisy: with R = 2 the estimate is below 60% of the
    true value about 45% of the time (R = 3: 30%, R = 5: 16%, R = 10: 5%). A band that is too narrow lets
    noise through as gains, so fewer than 5 repeats warn. ``small_sample=True`` replaces ``z = 2`` by the
    Student-t quantile with the same coverage for R - 1 degrees of freedom (4.53 at R = 3, 2.65 at R = 6),
    which keeps the false-keep rate near the nominal 2.3% at any R; it applies only when ``z == 2``.
    """
    s = np.asarray(scores, float)
    if len(s) < 2:
        raise ValueError("need at least two repeated evaluations")
    sd = float(np.std(s, ddof=1)) * math.sqrt(2)
    mult = z
    if small_sample:
        if z != 2.0:
            raise ValueError("small_sample=True uses the t quantile matching z = 2; leave z at 2")
        mult = _T_Z2.get(len(s) - 1, z)
    elif len(s) < MIN_RELIABLE_REPEATS:
        warnings.warn(
            f"noise band from only {len(s)} repeated evaluations: the band itself is noisy and often too narrow "
            f"(noise passes as gains). Use >= {MIN_RELIABLE_REPEATS} repeats, or small_sample=True for a "
            "Student-t band",
            UserWarning,
            stacklevel=2,
        )
    return NoiseEstimate(mult * sd, sd, mult, "repeat", len(s))


def noise_from_trials(
    matrix: np.ndarray, z: float = 2.0, reps: int = 2000, seed: int = 0, small_k_correction: bool = False
) -> NoiseEstimate:
    """delta from one tasks x k (k >= 2) evaluation by RRSI's within-task bootstrap: sd(null dS) = sqrt(2) * se."""
    m = np.asarray(matrix, float)
    if m.ndim != 2 or m.shape[1] < 2:
        raise ValueError("need a tasks x k matrix with k >= 2")
    rng = np.random.default_rng(seed)
    n_tasks, k = m.shape
    idx = rng.integers(0, k, size=(reps, n_tasks, k))
    boot = np.take_along_axis(np.broadcast_to(m, (reps, n_tasks, k)), idx, axis=2).mean(axis=(1, 2))
    sd = math.sqrt(2) * float(np.std(boot, ddof=1))
    if small_k_correction:  # the plug-in bootstrap underestimates variance by (k-1)/k; off in RRSI's code
        sd *= math.sqrt(k / (k - 1))
    return NoiseEstimate(z * sd, sd, z, "bootstrap" + ("+small_k" if small_k_correction else ""), n_tasks * k)


def fixed_noise(delta: float) -> NoiseEstimate:
    return NoiseEstimate(delta, delta / 2.0, 2.0, "fixed", 0)


def bootstrap_ci(
    x: Sequence[float], stat=np.mean, alpha: float = 0.05, reps: int = 5000, seed: int = 0
) -> tuple[float, float, float]:
    """(point, lo, hi) percentile bootstrap CI."""
    a = np.asarray(x, float)
    if len(a) == 0:
        return (float("nan"),) * 3
    if len(a) == 1:
        return (float(stat(a)),) * 3
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(a), (reps, len(a)))
    bs = a[idx].mean(axis=1) if stat is np.mean else np.array([stat(a[row]) for row in idx])
    return float(stat(a)), float(np.quantile(bs, alpha / 2)), float(np.quantile(bs, 1 - alpha / 2))


def paired_diff_ci(
    a: Sequence[float], b: Sequence[float], alpha: float = 0.05, reps: int = 5000, seed: int = 0
) -> dict:
    """Paired comparison b - a over the same tasks/seeds."""
    d = np.asarray(b, float) - np.asarray(a, float)
    point, lo, hi = bootstrap_ci(d, alpha=alpha, reps=reps, seed=seed)
    return {
        "mean_diff": point,
        "lo": lo,
        "hi": hi,
        "n": int(len(d)),
        "p_better": float(np.mean(d > 0)) if len(d) else float("nan"),
    }


def prob_better_bootstrap(
    cand: Sequence[float], best: Sequence[float], reps: int = 20000, seed: int = 1234, lower_is_better: bool = False
) -> float:
    """P(mean(cand) beats mean(best)) by independent bootstrap (autoresearch-mlx ``rigor.py``)."""
    c, b = np.asarray(cand, float), np.asarray(best, float)
    rng = np.random.default_rng(seed)
    mc = c[rng.integers(0, len(c), (reps, len(c)))].mean(1)
    mb = b[rng.integers(0, len(b), (reps, len(b)))].mean(1)
    return float(np.mean(mc < mb) if lower_is_better else np.mean(mc > mb))


def beta_lcb(successes: float, failures: float, q: float = 0.05, prior: tuple[float, float] = (1.0, 1.0)) -> float:
    """Lower q-quantile of Beta(prior_a + s, prior_b + f); a normal approximation without scipy."""
    a, b = prior[0] + successes, prior[1] + failures
    try:
        from scipy.stats import beta as _beta

        return float(_beta.ppf(q, a, b))
    except Exception:  # noqa: BLE001
        mean = a / (a + b)
        var = a * b / ((a + b) ** 2 * (a + b + 1))
        zq = {0.05: 1.6449, 0.025: 1.96, 0.1: 1.2816}.get(q, 1.6449)
        return max(0.0, mean - zq * math.sqrt(var))


def spearman(x: Sequence[float], y: Sequence[float]) -> float:
    x, y = np.asarray(x, float), np.asarray(y, float)
    if len(x) < 2:
        return float("nan")
    rx, ry = _rank(x), _rank(y)
    if np.std(rx) == 0 or np.std(ry) == 0:
        return float("nan")
    return float(np.corrcoef(rx, ry)[0, 1])


def _rank(a: np.ndarray) -> np.ndarray:
    """Ranks from 0 with ties averaged."""
    ranks = np.empty(len(a))
    ranks[a.argsort(kind="mergesort")] = np.arange(len(a), dtype=float)
    vals, inv, counts = np.unique(a, return_inverse=True, return_counts=True)
    sums = np.zeros(len(vals))
    np.add.at(sums, inv, ranks)
    return (sums / counts)[inv]


def gini(values: Sequence[float]) -> float:
    v = np.sort(np.asarray(values, float))
    if len(v) == 0 or v.sum() == 0:
        return 0.0
    n = len(v)
    return float((2 * np.arange(1, n + 1) - n - 1).dot(v) / (n * v.sum()))


def mean_se(x: Sequence[float]) -> tuple[float, float]:
    a = np.asarray(x, float)
    if len(a) == 0:
        return float("nan"), float("nan")
    return float(a.mean()), float(a.std(ddof=1) / math.sqrt(len(a))) if len(a) > 1 else 0.0


def summarize_runs(values: Sequence[float], seed: int = 0) -> dict:
    """mean, 95% bootstrap CI and n: the standard way experiments report numbers."""
    p, lo, hi = bootstrap_ci(values, seed=seed)
    return {"mean": p, "lo": lo, "hi": hi, "n": len(values)}
