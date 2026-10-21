"""Autocorrelation inequalities on step functions [paper:App.A Problem 4]; default ``phi3`` = Table 1 [inferred]."""
from __future__ import annotations

import ast
import json
import random
from typing import Optional

import numpy as np

from ...core.artifact import Artifact
from ...dream.agent import AttemptContext, EvalOutcome, ParametricAgent
from .base import ProgramDomain, failure, parent_measured, pick_mechanism

PROGRAM = "construct.py"
VARIANTS = ("phi1", "phi2", "phi3")
MECHANISMS = ("coord", "gradient", "smooth", "upsample", "shake")
BEST_KNOWN = {"phi1": 1.5053, "phi2": 0.8962, "phi3": 1.4557}
SEED_N = 40

TEMPLATE = '''"""Autocorrelation inequality __VARIANT__: heights of a step function on [-1/4, 1/4] (equal steps).

INIT holds the starting heights (the parent's measured function); STAGE is the mechanism applied in
this attempt, [mechanism, params, seed] (or null).
"""
import math

import numpy as np

VARIANT = "__VARIANT__"
INIT = __INIT__
STAGE = __STAGE__


def knots(h):
    """(f*f) at the knots, f normalised to integral 1."""
    s = float(np.sum(h))
    n = len(h)
    return np.convolve(h, h) * (2.0 * n) / (s * s)


def loss(h):
    """The objective as a quantity to MINIMISE."""
    h = np.asarray(h, dtype=float)
    if VARIANT != "phi3" and np.any(h < 0):
        return math.inf
    s = float(np.sum(h))
    if abs(s) < 1e-12 or (VARIANT != "phi3" and s <= 0):
        return math.inf
    g = knots(h)
    if VARIANT == "phi3":
        return float(np.max(np.abs(g)))
    if VARIANT == "phi1":
        return float(np.max(g))
    w = 1.0 / (2.0 * len(h))
    gg = np.concatenate([[0.0], g, [0.0]])
    l2 = float(np.sum(w / 3.0 * (gg[:-1] ** 2 + gg[:-1] * gg[1:] + gg[1:] ** 2)))
    l1 = float(np.sum(w / 2.0 * (np.abs(gg[:-1]) + np.abs(gg[1:]))))
    return -l2 / (l1 * float(np.max(np.abs(g))))


def project(h):
    h = np.asarray(h, dtype=float)
    if VARIANT != "phi3":
        h = np.clip(h, 0.0, None)
    s = float(np.sum(h))
    return h * (len(h) / s) if abs(s) > 1e-12 else np.ones(len(h))


def grad(h, tau):
    """Gradient of the soft-max (temperature tau) of the knot values (phi1 / phi3), or of the
    exact loss by coordinate finite differences on a random subset (phi2)."""
    n = len(h)
    s = float(np.sum(h))
    if VARIANT == "phi2":
        base = loss(h)
        g = np.zeros(n)
        for i in range(n):
            e = np.zeros(n)
            e[i] = 1e-4
            g[i] = (loss(h + e) - base) / 1e-4
        return g
    c = np.convolve(h, h)
    k = 2.0 * n / (s * s)
    v = c * k
    a = np.abs(v) if VARIANT == "phi3" else v
    z = tau * (a - a.max())
    p = np.exp(z)
    p /= p.sum()
    q = p * (np.sign(v) if VARIANT == "phi3" else 1.0)
    F = float(np.sum(p * a))
    return 2.0 * k * np.correlate(q, h, mode="valid") - 2.0 * F / s


def descend(h, rng, steps=40, lr=0.05, tau=60.0, jitter=0.02):
    best, bl = h.copy(), loss(h)
    cur = project(h * (1.0 + rng.normal(0.0, jitter, len(h)))) if jitter else h.copy()   # break symmetry
    for _ in range(steps):
        g = grad(cur, tau)
        gn = float(np.linalg.norm(g)) or 1.0
        cand = project(cur - lr * g / gn * math.sqrt(len(cur)))
        cl = loss(cand)
        if cl < bl:
            best, bl, cur = cand, cl, cand
        else:
            lr *= 0.6
            cur = best.copy()
    return best


def coord(h, rng, iters=150, scale=0.3):
    best, bl = h.copy(), loss(h)
    for _ in range(iters):
        cand = best.copy()
        i = rng.integers(len(cand))
        cand[i] += rng.normal(0.0, scale) * max(1e-3, abs(cand[i]) + 0.1)
        cand = project(cand)
        cl = loss(cand)
        if cl < bl:
            best, bl = cand, cl
    return best


def smooth(h, rng, width=3, steps=20):
    k = np.ones(width) / width
    hs = project(np.convolve(h, k, mode="same"))
    out = descend(hs, rng, steps=steps)
    return out if loss(out) < loss(h) else h


def upsample(h, rng, steps=30, noise=0.02):
    if len(h) >= 640:
        return descend(h, rng, steps=steps)
    hu = project(np.repeat(h, 2) * (1.0 + rng.normal(0.0, noise, 2 * len(h))))
    out = descend(hu, rng, steps=steps)
    return out if loss(out) < loss(h) else h


def shake(h, rng, frac=0.25, sigma=0.4, steps=30):
    n = len(h)
    m = max(2, int(frac * n))
    i = int(rng.integers(0, n - m + 1))
    cand = h.copy()
    cand[i:i + m] *= np.exp(rng.normal(0.0, sigma, m))
    out = descend(project(cand), rng, steps=steps)
    return out if loss(out) < loss(h) else h


MECHS = {"coord": coord, "gradient": descend, "smooth": smooth, "upsample": upsample, "shake": shake}


def construct():
    h = project(np.asarray(INIT, dtype=float))
    if STAGE is not None:
        mech, params, seed = STAGE
        h = MECHS[mech](h, np.random.default_rng(seed), **params)
    return [float(x) for x in h]
'''


def program(variant: str, init: list, stage) -> str:
    return (TEMPLATE.replace("__VARIANT__", variant).replace("__INIT__", json.dumps([float(x) for x in init]))
            .replace("__STAGE__", repr(stage) if stage is not None else "None"))


def objectives(h) -> dict:
    """Exact Phi1 / Phi2 / Phi3 from the knots of the piecewise-linear autoconvolution (f normalised to 1)."""
    h = np.asarray(h, dtype=float)
    n = len(h)
    s = float(h.sum())
    g = np.convolve(h, h) * (2.0 * n) / (s * s)
    w = 1.0 / (2.0 * n)
    gg = np.concatenate([[0.0], g, [0.0]])
    l2 = float(np.sum(w / 3.0 * (gg[:-1] ** 2 + gg[:-1] * gg[1:] + gg[1:] ** 2)))
    l1 = float(np.sum(w / 2.0 * (gg[:-1] + gg[1:])))  # phi2 needs f >= 0, so f*f >= 0 (trapezoids exact)
    linf = float(np.max(np.abs(g)))
    return {"phi1": float(np.max(g)), "phi2": l2 / (l1 * linf) if l1 > 0 and linf > 0 else 0.0, "phi3": linf}


class AutocorrelationDomain(ProgramDomain):
    """``variant``: ``phi1`` / ``phi3`` (minimised, score = -Phi) or ``phi2`` (maximised, score = Phi)."""

    name = "autocorr"
    program_file = PROGRAM
    entry = "construct"

    def __init__(self, variant: str = "phi3", *, max_n: int = 2000, sandboxed: bool = True,
                 timeout_s: float = 60.0) -> None:
        if variant not in VARIANTS:
            raise ValueError(f"variant must be one of {VARIANTS}")
        super().__init__(sandboxed=sandboxed, timeout_s=timeout_s)
        self.variant, self.max_n = variant, max_n
        self.name = f"autocorr_{variant}"

    def describe(self) -> str:
        what = {"phi1": "a NON-NEGATIVE f minimising Phi1(f) = max_t (f*f)(t)",
                "phi2": "a NON-NEGATIVE f maximising Phi2(f) = ||f*f||_2^2 / (||f*f||_1 ||f*f||_inf)",
                "phi3": "a (possibly signed) f minimising Phi3(f) = max_t |(f*f)(t)|"}[self.variant]
        sign = "score = Phi2 (higher is better)" if self.variant == "phi2" else \
            f"score = -{self.variant.capitalize()} (higher score = lower {self.variant.capitalize()})"
        return (f"Autocorrelation inequality ({self.variant}): write construct.py whose construct() returns the "
                f"heights h_0..h_(n-1) of a step function f with n equal steps on [-1/4, 1/4] (2 <= n <= "
                f"{self.max_n}); f is rescaled to integral 1 and (f*f)(t) = int f(t-x) f(x) dx. Find {what}. The "
                f"evaluator computes the objective exactly from the discrete autoconvolution; {sign}. Best "
                f"published value: {BEST_KNOWN[self.variant]}. Runtime <= {self.timeout_s:.0f} s; invalid output "
                "scores 0 with a failure class.")

    def seed_artifact(self) -> Artifact:
        return Artifact({PROGRAM: program(self.variant, [1.0] * SEED_N, None)})

    def directions(self) -> list[str]:
        return list(MECHANISMS)

    def check(self, output, *, seed: int = 0) -> EvalOutcome:
        if not isinstance(output, list) or not all(isinstance(v, (int, float)) for v in output):
            return failure("correctness", "construct() must return a list of numbers")
        h = np.asarray(output, dtype=float)
        if not 2 <= h.size <= self.max_n or not np.all(np.isfinite(h)):
            return failure("constraint", f"need 2..{self.max_n} finite heights, got {h.size}")
        if self.variant != "phi3" and np.any(h < 0):
            return failure("constraint", "f must be non-negative for this inequality")
        s = float(h.sum())
        if (self.variant != "phi3" and s <= 0) or abs(s) < 1e-12:
            return failure("constraint", "f cannot be normalised to integral 1")
        obj = objectives(h)
        val = obj[self.variant]
        score = val if self.variant == "phi2" else -val
        return EvalOutcome(score, True, True, "ok", None, 1, 1,
                           {self.variant: val, "n": int(h.size), "best_known": BEST_KNOWN[self.variant],
                            "heights": [float(x) for x in h]})

    def mock_agent(self, **kw) -> ParametricAgent:
        variant = self.variant

        def mutate(parent, rng, ctx):
            return _mutate(parent, rng, ctx, variant)

        return ParametricAgent(mutate, name=f"autocorr-{variant}-mock", **kw)


def _mutate(parent: Artifact, rng: random.Random, ctx: Optional[AttemptContext], variant: str):
    init = ast.literal_eval(parent[PROGRAM].split("INIT = ", 1)[1].split("\n", 1)[0])
    init = parent_measured(ctx, "heights", init)  # resume from the parent's measured function
    mech = pick_mechanism(ctx, rng, MECHANISMS, 0.25)
    params = {"coord": {"iters": rng.choice([40, 80]), "scale": rng.choice([0.2, 0.4])},
              "gradient": {"steps": rng.choice([8, 15]), "lr": rng.choice([0.02, 0.05]),
                           "tau": rng.choice([30.0, 80.0])},
              "smooth": {"width": rng.choice([3, 5]), "steps": rng.choice([6, 12])},
              "upsample": {"steps": rng.choice([6, 12]), "noise": rng.choice([0.01, 0.05])},
              "shake": {"frac": rng.choice([0.15, 0.3]), "sigma": rng.choice([0.2, 0.5]),
                        "steps": rng.choice([6, 12])}}[mech]
    stage = [mech, params, rng.randrange(10 ** 6)]
    text = f"# {mech} {params}\n\nMechanism: {mech}; resumes the parent's measured function ({len(init)} steps)."
    return Artifact({PROGRAM: program(variant, init, stage)}), text


__all__ = ["AutocorrelationDomain", "objectives", "program", "BEST_KNOWN", "MECHANISMS", "VARIANTS"]
