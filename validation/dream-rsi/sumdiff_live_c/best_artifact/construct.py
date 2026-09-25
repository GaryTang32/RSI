"""Sum-difference construction (maximize log(|A+A|/|A|) / log(|A-A|/|A|)).

Two-stage optimization: simulated annealing (exploration) + hill-climbing (refinement).
Anneal is primary (respects 'anneal' branch direction); hill provides greedy polish.
"""
import math
import random

INIT = list(range(12))  # (construct() overrides)
STAGES = []  # (not used; construct() is the entry point)


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
    """Two-stage optimization: simulated annealing + hill-climbing.
    
    Respects the 'anneal' branch direction by using simulated annealing
    as the primary mechanism for broad exploration. Hill-climbing then
    provides greedy refinement of each candidate.
    
    Key insight from history:
    - Pure hill-climbing (t1_b2_a2) achieved 1.0281
    - Anneal-only (b0_a0) achieved 1.0
    - Hybrid anneal+hill was used in t1_b0_a0 (1.0) but with fringe as primary
    
    This attempt is new: AP → anneal (primary) → hill (refinement)
    
    Strategy:
    1. Try multiple AP starting sizes (6-20) to explore different scales
    2. For each size, try multiple annealing temps (0.2, 0.5, 1.0)
       to explore different exploration/exploitation trade-offs
    3. Anneal each: probabilistic Metropolis acceptance with temperature
       decay allows escaping local optima
    4. Hill each result: greedy improvement finds peaks from anneal's candidates
    
    Expected: Two-stage pipeline (exploration + refinement) should find
    better structures than either alone, potentially exceeding baseline 1.0281.
    Anneal explores via probabilistic acceptance; hill polishes greedily.
    """
    best_A = None
    best_gamma = -1.0
    
    sizes = [6, 8, 10, 12, 14, 16, 18, 20]
    temps = [0.2, 0.5, 1.0]  # Multiple exploration regimes
    
    seed = 42
    for size in sizes:
        for t0 in temps:
            A = set(range(size))
            rng = random.Random(seed)
            # Stage 1: Anneal for exploration (primary, respects branch direction)
            A = anneal(A, rng, iters=150, t0=t0)
            # Stage 2: Hill for greedy refinement
            A = hill(A, rng, iters=150)
            g = gamma(A)
            if g > best_gamma or best_A is None:
                best_A = A
                best_gamma = g
            seed += 1
    
    return sorted(best_A)
