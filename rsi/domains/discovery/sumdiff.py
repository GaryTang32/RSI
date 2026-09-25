"""Sum-difference sets: maximize Gamma(A) = log(|A+A|/|A|) / log(|A-A|/|A|) [paper:App.A].

The artifact is ``construct.py`` defining ``construct() -> list[int]``. The locked
evaluator runs it, checks the output (distinct integers, 2 <= |A| <= ``max_size``,
span <= ``max_span``, non-degenerate |A-A| > |A|) and scores Gamma(A) computed with
FFT-free exact convolutions of indicator vectors. Gamma > 1 means "more sums than
differences" (MSTD); the paper's best-known values are ~1.14 for very large sets.

Mock discovery agent: the seed program is a *staged construction recipe*
(``INIT`` + ``STAGES``); each attempt appends one mechanism stage to its parent's
recipe (so a child resumes exactly where its parent's workspace ended):
``hill`` (add/remove/shift hill climb), ``anneal`` (simulated annealing),
``window`` (exhaustive re-optimisation of a small fringe window), ``fringe``
(re-sample the fringes around a dense middle), ``grow`` (extend the span with
accepted additions). Direction tags pick the mechanism of a branch.
"""
from __future__ import annotations

import json
import math
import random
from typing import Optional

import numpy as np

from ...core.artifact import Artifact
from ...dream.agent import AttemptContext, EvalOutcome, ParametricAgent
from .base import ProgramDomain

PROGRAM = "construct.py"
MECHANISMS = ("hill", "anneal", "window", "fringe", "grow")

TEMPLATE = '''"""Sum-difference construction (maximize log(|A+A|/|A|) / log(|A-A|/|A|)).

INIT is the starting set; STAGES are applied in order, each [mechanism, params, seed].
"""
import math
import random

INIT = __INIT__
STAGES = __STAGES__


def gamma(A):
    A = sorted(A)
    n = len(A)
    if n < 2:
        return 0.0
    s = set()
    d = set()
    for a in A:
        for b in A:
            s.add(a + b)
            d.add(a - b)
    ns, nd = len(s), len(d)
    if nd <= n or ns <= n:
        return 0.0
    return math.log(ns / n) / math.log(nd / n)


def _neighbour(A, rng, moves):
    B = set(A)
    lo, hi = min(B), max(B)
    m = rng.choice(moves)
    if m == "add":
        B.add(rng.randint(lo - 3, hi + 3))
    elif m == "remove" and len(B) > 4:
        B.discard(rng.choice(sorted(B)))
    else:
        x = rng.choice(sorted(B))
        B.discard(x)
        B.add(x + rng.choice([-3, -2, -1, 1, 2, 3]))
    return B


def hill(A, rng, iters=120, moves=("add", "remove", "shift")):
    cur = gamma(A)
    for _ in range(iters):
        B = _neighbour(A, rng, moves)
        g = gamma(B)
        if g >= cur:
            A, cur = B, g
    return A


def anneal(A, rng, iters=150, t0=0.02):
    cur = gamma(A)
    best, bg = set(A), cur
    for i in range(iters):
        T = t0 * (1 - i / iters) + 1e-6
        B = _neighbour(A, rng, ("add", "remove", "shift"))
        g = gamma(B)
        if g >= cur or rng.random() < math.exp((g - cur) / T):
            A, cur = B, g
            if g > bg:
                best, bg = set(B), g
    return best


def window(A, rng, width=7):
    A = sorted(A)
    left = rng.random() < 0.5
    base = A[0] if left else A[-1] - width + 1
    fixed = [a for a in A if not (base <= a < base + width)]
    best, bg = set(A), gamma(A)
    for mask in range(1 << width):
        B = set(fixed) | {base + i for i in range(width) if mask >> i & 1}
        if len(B) < 4:
            continue
        g = gamma(B)
        if g > bg:
            best, bg = B, g
    return best


def fringe(A, rng, tries=40, k=6):
    A = sorted(A)
    best, bg = set(A), gamma(A)
    lo, hi = A[0], A[-1]
    for _ in range(tries):
        B = set(a for a in A if lo + k <= a <= hi - k)
        B |= {lo + i for i in range(k) if rng.random() < 0.5} | {hi - i for i in range(k) if rng.random() < 0.5}
        B |= {lo, hi}
        g = gamma(B)
        if g > bg:
            best, bg = B, g
    return best


def grow(A, rng, tries=60):
    cur = gamma(A)
    for _ in range(tries):
        lo, hi = min(A), max(A)
        B = set(A)
        B.add(hi + rng.randint(1, 4) if rng.random() < 0.5 else lo - rng.randint(1, 4))
        g = gamma(B)
        if g >= cur:
            A, cur = B, g
    return A


MECHS = {"hill": hill, "anneal": anneal, "window": window, "fringe": fringe, "grow": grow}


def construct():
    A = set(INIT)
    for mech, params, seed in STAGES:
        A = MECHS[mech](set(A), random.Random(seed), **params)
    return sorted(A)
'''

SEED_SET = [0, 6, 10, 18, 19, 21, 22, 33, 36, 37, 43, 44]   # Gamma = 0.9105; progress spans several rounds


def program(init: list[int], stages: list) -> str:
    return TEMPLATE.replace("__INIT__", json.dumps(init)).replace("__STAGES__", json.dumps(stages))


def gamma(A) -> tuple[float, int, int, int]:
    a = np.unique(np.asarray(list(A), dtype=np.int64))
    n = int(a.size)
    if n < 2:
        return 0.0, n, 0, 0
    x = a - a.min()
    ind = np.zeros(int(x.max()) + 1)
    ind[x] = 1.0
    s = int((np.convolve(ind, ind) > 0.5).sum())
    d = int((np.convolve(ind, ind[::-1]) > 0.5).sum())
    if d <= n or s <= n:
        return 0.0, n, s, d
    return math.log(s / n) / math.log(d / n), n, s, d


class SumDiffDomain(ProgramDomain):
    name = "sumdiff"
    program_file = PROGRAM
    entry = "construct"

    def __init__(self, *, max_size: int = 400, max_span: int = 20000, sandboxed: bool = True,
                 timeout_s: float = 30.0) -> None:
        super().__init__(sandboxed=sandboxed, timeout_s=timeout_s)
        self.max_size, self.max_span = max_size, max_span

    def describe(self) -> str:
        return ("Sum-difference problem: write construct.py whose construct() returns a finite set A of distinct "
                "integers maximizing Gamma(A) = log(|A+A|/|A|) / log(|A-A|/|A|) (higher is better; > 1 means more "
                f"sums than differences). Constraints: 2 <= |A| <= {self.max_size}, max(A) - min(A) <= {self.max_span}, "
                "runtime <= 30 s. Invalid output scores 0.")

    def seed_artifact(self) -> Artifact:
        return Artifact({PROGRAM: program(SEED_SET, [])})

    def directions(self) -> list[str]:
        return list(MECHANISMS)

    def check(self, output, *, seed: int = 0) -> EvalOutcome:
        if not isinstance(output, list) or not all(isinstance(v, (int, float)) and float(v).is_integer()
                                                    for v in output):
            return EvalOutcome(0.0, True, False, "correctness", "construct() must return a list of integers", 0, 1)
        A = sorted({int(v) for v in output})
        if len(A) != len(output):
            return EvalOutcome(0.0, True, False, "constraint", "elements must be distinct", 0, 1)
        if not 2 <= len(A) <= self.max_size or A[-1] - A[0] > self.max_span:
            return EvalOutcome(0.0, True, False, "constraint",
                               f"|A|={len(A)} or span={A[-1] - A[0] if A else 0} out of bounds", 0, 1)
        g, n, s, d = gamma(A)
        if g <= 0:
            return EvalOutcome(0.0, True, False, "constraint", "degenerate set (|A-A| <= |A|)", 0, 1)
        return EvalOutcome(g, True, True, "ok", None, 1, 1, {"size": n, "sums": s, "diffs": d, "mstd": s > d})

    # ---- mock discovery agent
    def mock_agent(self, **kw) -> ParametricAgent:
        return ParametricAgent(_mutate, name="sumdiff-mock", **kw)


def _parse(code: str) -> tuple[list, list]:
    init = json.loads(code.split("INIT = ", 1)[1].split("\n", 1)[0])
    stages = json.loads(code.split("STAGES = ", 1)[1].split("\n", 1)[0])
    return init, stages


def _mutate(parent: Artifact, rng: random.Random, ctx: Optional[AttemptContext]):
    init, stages = _parse(parent[PROGRAM])
    mech = (ctx.direction.get("direction") if ctx and ctx.direction else None) or rng.choice(MECHANISMS)
    if ctx and ctx.attempt > 0 and rng.random() < 0.25:
        mech = rng.choice(MECHANISMS)       # the agent sometimes switches mechanism within a branch
    # one attempt = a modest amount of search (calibrated so progress spans several rounds)
    params = {"hill": {"iters": rng.choice([15, 30, 45])}, "anneal": {"iters": rng.choice([20, 40]),
                                                                       "t0": rng.choice([0.01, 0.02, 0.05])},
              "window": {"width": rng.choice([3, 4, 5])}, "fringe": {"tries": rng.choice([5, 10]),
                                                                     "k": rng.choice([3, 4, 6])},
              "grow": {"tries": rng.choice([8, 15])}}[mech]
    stages = stages + [[mech, params, rng.randrange(10 ** 6)]]
    text = f"# apply {mech} {params}\n\nMechanism: {mech}; builds on the parent's construction ({len(stages)} stages)."
    return Artifact({PROGRAM: program(init, stages)}), text
