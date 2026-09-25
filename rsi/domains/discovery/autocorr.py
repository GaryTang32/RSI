"""Autocorrelation inequalities [paper:App.A Problem 4]: step functions on [-1/4, 1/4].

For an integrable f supported on [-1/4, 1/4] with integral 1, (f*f)(t) = int f(t-x) f(x) dx:

* ``phi1`` (first inequality): f >= 0, minimise Phi1(f) = max_t (f*f)(t);
* ``phi2`` (second inequality): f >= 0, maximise Phi2(f) = ||f*f||_2^2 / (||f*f||_1 ||f*f||_inf);
* ``phi3`` (third inequality): f may be signed, minimise Phi3(f) = max_t |(f*f)(t)|.

Table 1's "Auto Correlation (lower is better)" column (Dream 1.456375, Fixed 1.456001, SimpleTES
1.453675, AlphaEvolve 1.455700) matches AlphaEvolve's published bound for the THIRD inequality
(C3 <= 1.4557; its first-inequality bound is 1.5053), so ``phi3`` is the default [inferred: the paper
does not say which of the three it reports].

The artifact is ``construct.py`` defining ``construct() -> list[float]``: the heights h_0..h_{n-1} of
a step function with n equal steps of width w = 1/(2n) on [-1/4, 1/4]. f*f of such a function is
piecewise linear with knots at multiples of w, so the evaluator computes every objective EXACTLY
from the discrete autoconvolution c = h (*) h: with f normalised to integral 1, the knot values are
(f*f)(t_k) = 2n c_k / (sum h)^2, and Phi1 = max_k, Phi3 = max_k |.|, and Phi2 from the exact L1/L2
norms of the piecewise-linear f*f. Scores follow the framework convention "larger is better":
``-Phi1``, ``Phi2``, ``-Phi3``. The constant function scores Phi1 = Phi3 = 2 and Phi2 = 2/3.

Mock discovery agent (every attempt resumes the parent's measured function, stored in its
``eval/score.json``, and applies one mechanism): ``coord`` (coordinate hill climbing), ``gradient``
(descent on a soft-max of the objective), ``smooth`` (smoothing, then descent), ``upsample`` (double
the resolution, then descent) and ``shake`` (perturb a window strongly, then descent). Each stage is a
modest amount of search, so progress spans several rounds.
"""
from __future__ import annotations

import ast
import json
import random
from typing import Optional

import numpy as np

from ...core.artifact import Artifact
from ...dream.agent import AttemptContext, EvalOutcome, ParametricAgent
from .base import ProgramDomain

PROGRAM = "construct.py"
VARIANTS = ("phi1", "phi2", "phi3")
MECHANISMS = ("coord", "gradient", "smooth", "upsample", "shake")
#: best published values (AlphaEvolve 2025): Phi1 <= 1.5053, Phi2 >= 0.8962, Phi3 <= 1.4557
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
    """Exact Phi1 / Phi2 / Phi3 of the step function with heights ``h`` (normalised to integral 1)."""
    h = np.asarray(h, dtype=float)
    n = len(h)
    s = float(h.sum())
    g = np.convolve(h, h) * (2.0 * n) / (s * s)
    w = 1.0 / (2.0 * n)
    gg = np.concatenate([[0.0], g, [0.0]])
    l2 = float(np.sum(w / 3.0 * (gg[:-1] ** 2 + gg[:-1] * gg[1:] + gg[1:] ** 2)))
    l1 = float(np.sum(w / 2.0 * (gg[:-1] + gg[1:])))      # phi2 needs f >= 0, so f*f >= 0 (trapezoids exact)
    linf = float(np.max(np.abs(g)))
    return {"phi1": float(np.max(g)), "phi2": l2 / (l1 * linf) if l1 > 0 and linf > 0 else 0.0, "phi3": linf}


class AutocorrelationDomain(ProgramDomain):
    """Problem 4 of App. A; ``variant`` in ``phi1`` / ``phi2`` / ``phi3`` (default, Table 1's column)."""

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
            return EvalOutcome(0.0, True, False, "correctness", "construct() must return a list of numbers", 0, 1)
        h = np.asarray(output, dtype=float)
        if not 2 <= h.size <= self.max_n or not np.all(np.isfinite(h)):
            return EvalOutcome(0.0, True, False, "constraint", f"need 2..{self.max_n} finite heights, got {h.size}",
                               0, 1)
        if self.variant != "phi3" and np.any(h < 0):
            return EvalOutcome(0.0, True, False, "constraint", "f must be non-negative for this inequality", 0, 1)
        s = float(h.sum())
        if (self.variant != "phi3" and s <= 0) or abs(s) < 1e-12:
            return EvalOutcome(0.0, True, False, "constraint", "f cannot be normalised to integral 1", 0, 1)
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


def _parse_init(code: str) -> list:
    return ast.literal_eval(code.split("INIT = ", 1)[1].split("\n", 1)[0])


def _mutate(parent: Artifact, rng: random.Random, ctx: Optional[AttemptContext], variant: str):
    init = _parse_init(parent[PROGRAM])
    if ctx is not None:  # resume from the parent's measured function (its workspace's eval/score.json)
        try:
            sj = json.loads(ctx.parent_workspace.get("eval/score.json") or "{}")
            init = (sj.get("diagnostics") or {}).get("heights") or init
        except json.JSONDecodeError:
            pass
    mech = (ctx.direction.get("direction") if ctx and ctx.direction else None) or rng.choice(MECHANISMS)
    if ctx and ctx.attempt > 0 and rng.random() < 0.25:
        mech = rng.choice(MECHANISMS)          # the agent sometimes switches mechanism within a branch
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


def phi_of_program(art: Artifact, variant: str = "phi3") -> float:
    """Convenience for tests: run a construct.py in-process and return its objective."""
    ns: dict = {"__name__": "candidate"}
    exec(compile(art[PROGRAM], PROGRAM, "exec"), ns)  # noqa: S102 - trusted mock programs
    return objectives(ns["construct"]())[variant]


__all__ = ["AutocorrelationDomain", "objectives", "program", "BEST_KNOWN", "MECHANISMS", "VARIANTS",
           "phi_of_program"]
