"""Sum-difference construction.

Maximize log(|A+A|/|A|) / log(|A-A|/|A|) by applying optimization stages
in sequence. INIT is the starting set; STAGES define the pipeline.
"""

import math
import random


INIT = [0, 6, 10, 18, 19, 21, 22, 33, 36, 37, 43, 44]

STAGES = [
    ("anneal", {"iters": 350, "t0": 0.04}, 42),
    ("grow", {"tries": 120}, 43),
    ("window", {"width": 8}, 44),
    ("anneal", {"iters": 150, "t0": 0.015}, 45),
    ("grow", {"tries": 100}, 46),
    ("hill", {"iters": 300}, 47),
]


def gamma(A):
    """Compute gamma: log(|A+A|/|A|) / log(|A-A|/|A|)."""
    A_sorted = sorted(A)
    n = len(A_sorted)
    if n < 2:
        return 0.0
    
    sums_set = set()
    diffs_set = set()
    
    for a in A_sorted:
        for b in A_sorted:
            sums_set.add(a + b)
            diffs_set.add(a - b)
    
    num_sums = len(sums_set)
    num_diffs = len(diffs_set)
    
    if num_diffs <= n or num_sums <= n:
        return 0.0
    
    return math.log(num_sums / n) / math.log(num_diffs / n)


def _neighbour(A, rng, moves):
    """Generate a random neighbor of A by applying one of the moves."""
    B = set(A)
    lo = min(B)
    hi = max(B)
    
    move = rng.choice(moves)
    
    if move == "add":
        B.add(rng.randint(lo - 3, hi + 3))
    elif move == "remove" and len(B) > 4:
        B.discard(rng.choice(sorted(B)))
    else:  # shift
        x = rng.choice(sorted(B))
        B.discard(x)
        delta = rng.choice([-3, -2, -1, 1, 2, 3])
        B.add(x + delta)
    
    return B


def hill(A, rng, iters=120, moves=("add", "remove", "shift")):
    """Hill climbing: greedily accept improving moves."""
    current_gamma = gamma(A)
    current_set = set(A)
    
    for _ in range(iters):
        neighbor = _neighbour(current_set, rng, moves)
        neighbor_gamma = gamma(neighbor)
        
        if neighbor_gamma >= current_gamma:
            current_set = neighbor
            current_gamma = neighbor_gamma
    
    return current_set


def anneal(A, rng, iters=150, t0=0.02):
    """Simulated annealing with temperature decay."""
    current_gamma = gamma(A)
    current_set = set(A)
    best_set = set(A)
    best_gamma = current_gamma
    
    for i in range(iters):
        temperature = t0 * (1.0 - i / iters) + 1e-6
        neighbor = _neighbour(current_set, rng, ("add", "remove", "shift"))
        neighbor_gamma = gamma(neighbor)
        
        accept_prob = math.exp((neighbor_gamma - current_gamma) / temperature)
        
        if neighbor_gamma >= current_gamma or rng.random() < accept_prob:
            current_set = neighbor
            current_gamma = neighbor_gamma
            
            if neighbor_gamma > best_gamma:
                best_set = set(neighbor)
                best_gamma = neighbor_gamma
    
    return best_set


def window(A, rng, width=7):
    """Exhaustive window search over 2^width configurations."""
    A_sorted = sorted(A)
    
    use_left = rng.random() < 0.5
    if use_left:
        window_start = A_sorted[0]
    else:
        window_start = A_sorted[-1] - width + 1
    
    window_end = window_start + width
    
    fixed = [a for a in A_sorted if a < window_start or a >= window_end]
    
    best_set = set(A)
    best_gamma = gamma(A)
    
    for mask in range(1 << width):
        candidate = set(fixed)
        for i in range(width):
            if (mask >> i) & 1:
                candidate.add(window_start + i)
        
        if len(candidate) < 4:
            continue
        
        candidate_gamma = gamma(candidate)
        if candidate_gamma > best_gamma:
            best_set = candidate
            best_gamma = candidate_gamma
    
    return best_set


def fringe(A, rng, tries=40, k=6):
    """Fringe: vary boundary elements systematically."""
    A_sorted = sorted(A)
    best_set = set(A)
    best_gamma = gamma(A)
    
    lo = A_sorted[0]
    hi = A_sorted[-1]
    
    for _ in range(tries):
        candidate = set(a for a in A_sorted if lo + k <= a <= hi - k)
        
        for i in range(k):
            if rng.random() < 0.5:
                candidate.add(lo + i)
            if rng.random() < 0.5:
                candidate.add(hi - i)
        
        candidate.add(lo)
        candidate.add(hi)
        
        candidate_gamma = gamma(candidate)
        if candidate_gamma > best_gamma:
            best_set = candidate
            best_gamma = candidate_gamma
    
    return best_set


def grow(A, rng, tries=60):
    """Grow: incrementally add elements at boundaries."""
    current_gamma = gamma(A)
    current_set = set(A)
    
    for _ in range(tries):
        lo = min(current_set)
        hi = max(current_set)
        
        candidate = set(current_set)
        if rng.random() < 0.5:
            candidate.add(hi + rng.randint(1, 4))
        else:
            candidate.add(lo - rng.randint(1, 4))
        
        candidate_gamma = gamma(candidate)
        if candidate_gamma >= current_gamma:
            current_set = candidate
            current_gamma = candidate_gamma
    
    return current_set


MECHS = {
    "hill": hill,
    "anneal": anneal,
    "window": window,
    "fringe": fringe,
    "grow": grow,
}


def construct():
    """Apply the pipeline stages to optimize the set."""
    result = set(INIT)
    
    for mechanism_name, params, seed in STAGES:
        mechanism_func = MECHS[mechanism_name]
        rng = random.Random(seed)
        result = mechanism_func(result, rng, **params)
    
    return sorted(result)
