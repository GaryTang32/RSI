"""Estimate the noise band delta from evaluations of the unchanged base harness
(mirrors ``rrsi/calibrate.py``).

delta bounds |S_hat(H) - S_hat(H)| between two independent evaluations of the SAME
harness. With R >= 2 independent base evaluations the null difference is observed
directly (``sd_null = stdev(S_r) * sqrt(2)``, via :func:`rsi.core.stats.noise_from_repeats`);
with a single k-trial evaluation the standard error of S_hat is bootstrapped over
trials within each task (``reps = 2000``, ``seed = 7``; :func:`rsi.core.stats.noise_from_trials`
when all weights are 1) and ``sd_boot = sqrt(2) * se * sqrt(k_pooled / k_single)``.
Either way::

    delta = z * sd(null dS),   z = delta_z (2.0 by default),

"so an unchanged harness clears the floor S* - delta about 97.5% of the time".

Extension (off by default): ``small_k_correction`` rescales the within-task bootstrap
se by ``sqrt(k / (k - 1))``. The plug-in bootstrap underestimates the trial variance
by a factor (k-1)/k, i.e. by half at the paper's k = 2 (see experiment E10).
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Optional

import numpy as np

from ..core.stats import noise_from_repeats, noise_from_trials
from .evaluate import Measurement, TaskResult, aggregate


def bootstrap_se(ev: Measurement, reps: int = 2000, seed: int = 7) -> float:
    """se(S_hat) by resampling trials within each task (weights respected)."""
    tasks = [tr for tr in ev.per_task.values() if tr.rewards]
    if not tasks:
        return 0.0
    ks = {len(tr.rewards) for tr in tasks}
    unweighted = all(all(w == 1.0 for w in tr.weights) for tr in tasks)
    if unweighted and len(ks) == 1 and next(iter(ks)) >= 2:
        m = np.array([tr.rewards for tr in tasks], float)
        return noise_from_trials(m, z=1.0, reps=reps, seed=seed).sd_null / math.sqrt(2.0)
    rng = np.random.default_rng(seed)
    num = np.zeros(reps)
    den = np.zeros(reps)
    for tr in tasks:
        r = np.asarray(tr.rewards, float)
        w = np.asarray(tr.weights, float)
        idx = rng.integers(0, len(r), size=(reps, len(r)))
        num += (r[idx] * w[idx]).sum(1)
        den += w[idx].sum(1)
    vals = np.where(den > 0, num / np.maximum(den, 1e-12), 0.0)
    return float(np.std(vals)) if reps > 1 else 0.0


def pooled(evals: list[Measurement]) -> Measurement:
    """Concatenate the trials of several evaluations of the same harness."""
    per: dict[str, TaskResult] = {}
    for ev in evals:
        for t, tr in ev.per_task.items():
            p = per.setdefault(t, TaskResult(rewards=[], weights=[], tokens=[]))
            p.rewards += tr.rewards
            p.weights += tr.weights
            p.tokens += tr.tokens
            p.missing += tr.missing
    return aggregate("pooled", sum(e.k for e in evals), per)


def calibrate(evals: list[Measurement], z: float = 2.0, reps: int = 2000, seed: int = 7,
              small_k_correction: bool = False) -> dict:
    """delta from one or more evaluations of the base harness."""
    if not evals:
        raise ValueError("no base evaluations")
    observed: dict = {}
    sd_null = 0.0
    if len(evals) >= 2:
        scores = [e.S for e in evals]
        diffs = [abs(a - b) for i, a in enumerate(scores) for b in scores[i + 1:]]
        sd_null = noise_from_repeats(scores, z=z).sd_null
        method = "repeated base evaluations"
        observed = {"S_per_eval": scores, "max_abs_diff": max(diffs)}
    else:
        method = "bootstrap over trials of one base evaluation"
    pe = pooled(evals)
    se = bootstrap_se(pe, reps=reps, seed=seed)
    k_single = max(1, evals[0].k)
    sd_boot = math.sqrt(2.0) * se * math.sqrt(pe.k / k_single)
    if small_k_correction and pe.k > 1:
        sd_boot *= math.sqrt(pe.k / (pe.k - 1))
    sd_use = sd_null if (len(evals) >= 2 and sd_null > 0) else sd_boot
    return {"delta": round(z * sd_use, 6), "z": z, "sd_null": round(sd_use, 6),
            "sd_null_bootstrap": round(sd_boot, 6), "se_bootstrap": round(se, 6), "method": method,
            "n_evals": len(evals), "k": evals[0].k, "n_tasks": len(pe.per_task), "S_base": round(evals[0].S, 6),
            "C_base": evals[0].C, "small_k_correction": small_k_correction, **observed}


def write(path: str | Path, cal: dict) -> None:
    Path(path).write_text(json.dumps(cal, indent=1))


def read_delta(path: str | Path) -> Optional[float]:
    p = Path(path)
    if not p.exists():
        return None
    return float(json.loads(p.read_text())["delta"])
