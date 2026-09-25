"""Circle packing in the unit square, n = 26: maximize the sum of radii [paper:App.A].

The artifact is ``pack.py`` defining ``pack() -> [[x, y, r], ...]``. The locked
evaluator checks exactly n circles, containment ``r <= x <= 1 - r`` (same for y) and
non-overlap ``|c_i - c_j| >= r_i + r_j`` with tolerance ``tol`` (violations ->
``fail_class="constraint"``, score 0) and scores ``sum(r)``. Best known for n = 26:
2.635983 (AlphaEvolve / SimpleTES / Dream-RSI all report it).

Mock discovery agent: every attempt resumes from the parent's measured packing (the
centers in the parent workspace's ``eval/score.json``), applies one mechanism, and finishes with an SLSQP optimisation of (x, y, r)
and an exact LP for the radii given the centers (``scipy.optimize.linprog``), so
every output is feasible up to 1e-12. Each stage runs SLSQP on (x, y, r) and then the
exact LP for the radii. Mechanisms: ``rows`` (re-initialise from a row pattern),
``jitter``, ``polish`` (tiny perturbation, longer SLSQP), ``relocate``
(move the smallest circles into the largest gaps), ``shake`` (strong perturbation
of a subset).
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

PROGRAM = "pack.py"
N_CIRCLES = 26
BEST_KNOWN = 2.635983
MECHANISMS = ("rows", "jitter", "polish", "relocate", "shake")

TEMPLATE = '''"""Pack 26 circles in the unit square maximizing the sum of radii.

INIT holds the starting centers (the parent's packing, or null for the ROWS pattern);
STAGE is the mechanism applied this attempt, [mechanism, params, seed] (or null).
"""
import numpy as np
from scipy.optimize import linprog, minimize

N = 26
ROWS = __ROWS__
INIT = __INIT__
STAGE = __STAGE__


def init_rows(rows, rng, jitter=0.0):
    pts = []
    h = 1.0 / len(rows)
    for i, k in enumerate(rows):
        off = 0.5 / k if i % 2 == 0 else 0.25 / k
        for j in range(k):
            pts.append([min(0.98, off + j / k + 0.5 / k * (i % 2) * 0.0), (i + 0.5) * h])
    P = np.array(pts[:N], dtype=float)
    P += rng.normal(0.0, jitter, P.shape)
    return np.clip(P, 0.02, 0.98)


def lp_radii(P):
    n = len(P)
    rows, rhs = [], []
    for i in range(n):
        for j in range(i + 1, n):
            a = np.zeros(n)
            a[i] = a[j] = 1.0
            rows.append(a)
            rhs.append(float(np.hypot(*(P[i] - P[j]))))
    ub = np.minimum.reduce([P[:, 0], 1 - P[:, 0], P[:, 1], 1 - P[:, 1]])
    res = linprog(-np.ones(n), A_ub=np.array(rows), b_ub=np.array(rhs), bounds=list(zip([0.0] * n, ub)),
                  method="highs")
    r = np.clip(res.x if res.success else np.zeros(n), 0.0, None)
    # exact repair: the LP solver's feasibility tolerance (~1e-7) exceeds the checker's 1e-9
    r = np.minimum(r, ub)
    d = np.hypot(P[:, None, 0] - P[None, :, 0], P[:, None, 1] - P[None, :, 1])
    over = r[:, None] + r[None, :] - d
    np.fill_diagonal(over, -1.0)
    r = r - np.max(np.where(over > 0, over / 2.0, 0.0), axis=1)
    return np.maximum(r - 1e-12, 0.0)


IU = np.triu_indices(N, 1)


def _cons(z):
    x, y, r = z[:N], z[N:2 * N], z[2 * N:]
    a, b = IU
    pair = (x[a] - x[b]) ** 2 + (y[a] - y[b]) ** 2 - (r[a] + r[b]) ** 2
    return np.concatenate([pair, x - r, 1 - x - r, y - r, 1 - y - r])


def _jac(z):
    x, y, r = z[:N], z[N:2 * N], z[2 * N:]
    a, b = IU
    M = len(a)
    J = np.zeros((M + 4 * N, 3 * N))
    k = np.arange(M)
    dx, dy, s = x[a] - x[b], y[a] - y[b], r[a] + r[b]
    J[k, a], J[k, b] = 2 * dx, -2 * dx
    J[k, N + a], J[k, N + b] = 2 * dy, -2 * dy
    J[k, 2 * N + a], J[k, 2 * N + b] = -2 * s, -2 * s
    i = np.arange(N)
    for q, (col, sx) in enumerate(((0, 1), (0, -1), (N, 1), (N, -1))):
        J[M + q * N + i, col + i] = sx
        J[M + q * N + i, 2 * N + i] = -1
    return J


def optimize(P, r, iters=100):
    """SLSQP on (x, y, r): maximize sum(r) s.t. containment and non-overlap."""
    z0 = np.concatenate([P[:, 0], P[:, 1], np.clip(r, 0, 0.5)])
    res = minimize(lambda z: -z[2 * N:].sum(), z0, jac=lambda z: np.concatenate([np.zeros(2 * N), -np.ones(N)]),
                   constraints=[{"type": "ineq", "fun": _cons, "jac": _jac}],
                   bounds=[(0.0, 1.0)] * (2 * N) + [(0.0, 0.5)] * N, method="SLSQP",
                   options={"maxiter": iters, "ftol": 1e-10})
    return np.clip(np.stack([res.x[:N], res.x[N:2 * N]], 1), 0.0, 1.0)


def stage(P, mech, params, rng):
    if mech == "rows":
        P = init_rows(params["rows"], rng, params.get("jitter", 0.01))
    elif mech == "jitter":
        P = np.clip(P + rng.normal(0.0, params.get("sigma", 0.02), P.shape), 0.01, 0.99)
    elif mech == "relocate":
        r = lp_radii(P)
        cand = rng.uniform(0.02, 0.98, (400, 2))
        for i in np.argsort(r)[: params.get("k", 2)]:
            others = np.delete(P, i, axis=0)
            gap = np.min(np.hypot(*(cand[:, None, :] - others[None, :, :]).transpose(2, 0, 1)), axis=1)
            gap = np.minimum(gap, np.minimum.reduce([cand[:, 0], 1 - cand[:, 0], cand[:, 1], 1 - cand[:, 1]]))
            P[i] = cand[int(np.argmax(gap))]
    elif mech == "shake":
        idx = rng.choice(N, size=params.get("k", 6), replace=False)
        P[idx] = np.clip(P[idx] + rng.normal(0.0, params.get("sigma", 0.08), (len(idx), 2)), 0.02, 0.98)
    elif mech == "polish":
        P = np.clip(P + rng.normal(0.0, params.get("sigma", 0.002), P.shape), 0.01, 0.99)
    r = lp_radii(P)
    return optimize(P, r * 0.98, iters=params.get("iters", 60 if mech != "polish" else 100))


def pack():
    rng = np.random.default_rng(0)
    P = np.array(INIT, dtype=float) if INIT else init_rows(ROWS, rng)
    best_P, best = P, lp_radii(P).sum()
    if STAGE:
        mech, params, seed = STAGE
        P2 = stage(best_P.copy(), mech, params, np.random.default_rng(seed))
        if lp_radii(P2).sum() > best:
            best_P = P2
    r = lp_radii(best_P)
    return [[float(x), float(y), float(rad)] for (x, y), rad in zip(best_P, r)]
'''

SEED_ROWS = [5, 5, 6, 5, 5]


def program(rows, init=None, stage=None) -> str:
    return (TEMPLATE.replace("__ROWS__", repr(rows)).replace("__INIT__", repr(init))
            .replace("__STAGE__", repr(stage)))


class CirclePackingDomain(ProgramDomain):
    name = "circlepack"
    program_file = PROGRAM
    entry = "pack"

    def __init__(self, n: int = N_CIRCLES, *, tol: float = 1e-9, sandboxed: bool = True, timeout_s: float = 60.0):
        super().__init__(sandboxed=sandboxed, timeout_s=timeout_s)
        self.n, self.tol = n, tol

    def describe(self) -> str:
        return (f"Circle packing: write pack.py whose pack() returns {self.n} circles [[x, y, r], ...] inside the unit "
                "square (r <= x <= 1-r, r <= y <= 1-r) with no overlaps ((xi-xj)^2+(yi-yj)^2 >= (ri+rj)^2), "
                f"maximizing the sum of radii (best known for n=26: {BEST_KNOWN}). Violations (tolerance "
                f"{self.tol:g}) score 0. numpy and scipy are available; runtime <= 60 s.")

    def seed_artifact(self) -> Artifact:
        return Artifact({PROGRAM: program(SEED_ROWS)})

    def directions(self) -> list[str]:
        return list(MECHANISMS)

    def check(self, output, *, seed: int = 0) -> EvalOutcome:
        C = np.asarray(output, dtype=float)
        if C.shape != (self.n, 3) or not np.all(np.isfinite(C)):
            return EvalOutcome(0.0, True, False, "correctness", f"expected {self.n} x 3 finite values, got {C.shape}",
                               0, 1)
        x, y, r = C[:, 0], C[:, 1], C[:, 2]
        tol = self.tol
        if np.any(r < 0):
            return EvalOutcome(0.0, True, False, "constraint", "negative radius", 0, 1)
        cont = np.minimum.reduce([x - r, 1 - r - x, y - r, 1 - r - y])
        if cont.min() < -tol:
            return EvalOutcome(0.0, True, False, "constraint", f"circle outside the square by {-cont.min():.2e}", 0, 1)
        d = np.hypot(x[:, None] - x[None, :], y[:, None] - y[None, :])
        gap = d - (r[:, None] + r[None, :])
        np.fill_diagonal(gap, np.inf)
        if gap.min() < -tol:
            return EvalOutcome(0.0, True, False, "constraint", f"overlap of {-gap.min():.2e}", 0, 1)
        s = float(r.sum())
        return EvalOutcome(s, True, True, "ok", None, 1, 1, {"sum_r": s, "gap_to_best_known": BEST_KNOWN - s,
                                                            "centers": [[float(a), float(b)] for a, b in zip(x, y)]})

    def mock_agent(self, **kw) -> ParametricAgent:
        return ParametricAgent(_mutate, name="circlepack-mock", **kw)


def _parse(code: str):
    rows = ast.literal_eval(code.split("ROWS = ", 1)[1].split("\n", 1)[0])
    init = ast.literal_eval(code.split("INIT = ", 1)[1].split("\n", 1)[0])
    return rows, init


PATTERNS = ([5, 5, 6, 5, 5], [6, 5, 5, 5, 5], [5, 6, 5, 6, 4], [4, 5, 4, 5, 4, 4], [6, 7, 6, 7], [5, 5, 5, 5, 6],
            [4, 4, 5, 4, 5, 4], [6, 6, 7, 7])


def _mutate(parent: Artifact, rng: random.Random, ctx: Optional[AttemptContext]):
    rows, init = _parse(parent[PROGRAM])
    if ctx is not None:  # resume from the parent's measured packing (its workspace's eval/score.json)
        try:
            sj = json.loads(ctx.parent_workspace.get("eval/score.json") or "{}")
            init = (sj.get("diagnostics") or {}).get("centers") or init
        except json.JSONDecodeError:
            pass
    mech = (ctx.direction.get("direction") if ctx and ctx.direction else None) or rng.choice(MECHANISMS)
    if ctx and ctx.attempt > 0 and mech == "rows":
        mech = rng.choice(("jitter", "polish", "shake"))   # a branch re-initialises only once
    elif ctx and ctx.attempt > 0 and rng.random() < 0.2:
        mech = rng.choice(MECHANISMS[1:])
    params = {"rows": {"rows": list(rng.choice(PATTERNS)), "jitter": rng.choice([0.005, 0.01, 0.02])},
              "jitter": {"sigma": rng.choice([0.01, 0.02, 0.04])},
              "polish": {"sigma": rng.choice([0.001, 0.003, 0.006])},
              "relocate": {"k": rng.choice([1, 2, 3])},
              "shake": {"k": rng.choice([4, 6, 9]), "sigma": rng.choice([0.05, 0.1])}}[mech]
    stage = [mech, params, rng.randrange(10 ** 6)]
    text = f"# {mech} {params}\n\nMechanism: {mech}; resumes the parent's packing."
    return Artifact({PROGRAM: program(rows, init, stage)}), text
