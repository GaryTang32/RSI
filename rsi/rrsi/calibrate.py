"""Noise band delta = z * sd(null dS) from repeated or bootstrapped evaluations of the base harness."""
from __future__ import annotations

import math

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
    sd_boot = math.sqrt(2.0) * se * math.sqrt(pe.k / max(1, evals[0].k))
    if small_k_correction and pe.k > 1:
        sd_boot *= math.sqrt(pe.k / (pe.k - 1))  # the plug-in bootstrap underestimates variance by (k-1)/k
    sd_use = sd_null if (len(evals) >= 2 and sd_null > 0) else sd_boot
    out = {"delta": round(z * sd_use, 6), "z": z, "sd_null": round(sd_use, 6),
           "sd_null_bootstrap": round(sd_boot, 6), "se_bootstrap": round(se, 6), "method": method,
           "n_evals": len(evals), "k": evals[0].k, "n_tasks": len(pe.per_task), "S_base": round(evals[0].S, 6),
           "C_base": evals[0].C, "small_k_correction": small_k_correction, **observed}
    if out["delta"] == 0.0:
        out["warning"] = ("delta = 0: the noise band is degenerate (k = 1 with a single base evaluation, or a "
                          "deterministic grader). Set Config.delta, or use calibration_repeats >= 2 / k >= 2.")
    return out
