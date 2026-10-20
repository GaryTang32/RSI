"""Sum-difference construction (maximize log(|A+A|/|A|) / log(|A-A|/|A|)).

Grow-integrated ensemble with 2-3x higher iteration counts.
Combines best mechanism (grow, from 1.0349 sibling) with aggressive scaling (from 1.02508 sibling).
"""
import math
import random

INIT = [0, 6, 10, 18, 19, 21, 22, 33, 36, 37, 43, 44]


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
    """Grow-integrated ensemble with 2-3x higher iteration counts.
    
    Eight strategies combining proven grow-integration (b2.a0: 1.0349)
    with aggressive iteration counts (b0.a0: 1.02508). Uses only 0.097s
    of 30s available budget; scaling iterations from 150-250 to 400-700
    for anneal/hill and 60-100 to 250-350 for grow/fringe exploits headroom.
    """
    strategies = [
        # Grow + fringe + anneal (grow-first expansion)
        [
            ("grow", {"tries": 250}, 4001),
            ("fringe", {"tries": 150, "k": 3}, 4002),
            ("anneal", {"iters": 500}, 4003),
        ],
        # Hill + grow + fringe (local + expansion + boundary)
        [
            ("hill", {"iters": 500}, 4011),
            ("grow", {"tries": 250}, 4012),
            ("fringe", {"tries": 120, "k": 3}, 4013),
        ],
        # Fringe + grow + anneal (boundary-first, then expand, then global)
        [
            ("fringe", {"tries": 180, "k": 3}, 4021),
            ("grow", {"tries": 250}, 4022),
            ("anneal", {"iters": 600}, 4023),
        ],
        # Anneal + grow + hill (global + expansion + local refinement)
        [
            ("anneal", {"iters": 700}, 4031),
            ("grow", {"tries": 300}, 4032),
            ("hill", {"iters": 500}, 4033),
        ],
        # Window + grow + anneal (exhaustive band + expansion + global)
        [
            ("window", {"width": 6}, 4041),
            ("grow", {"tries": 250}, 4042),
            ("anneal", {"iters": 500}, 4043),
        ],
        # Hill + grow + window (dual refinement)
        [
            ("hill", {"iters": 500}, 4051),
            ("grow", {"tries": 300}, 4052),
            ("window", {"width": 8}, 4053),
        ],
        # Grow-heavy: grow → hill → anneal (aggressive expansion + refinement)
        [
            ("grow", {"tries": 350}, 4061),
            ("hill", {"iters": 600}, 4062),
            ("anneal", {"iters": 400}, 4063),
        ],
        # Anneal + hill + grow (explore → local → expand, reversed order)
        [
            ("anneal", {"iters": 600}, 4071),
            ("hill", {"iters": 500}, 4072),
            ("grow", {"tries": 300}, 4073),
        ],
    ]
    
    candidates = []
    for stages in strategies:
        A = set(INIT)
        for mech, params, seed in stages:
            A = MECHS[mech](set(A), random.Random(seed), **params)
        candidates.append(A)
    
    best = max(candidates, key=gamma)
    return sorted(best)
