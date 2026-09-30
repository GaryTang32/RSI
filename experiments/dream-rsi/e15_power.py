"""P1 - Power of E15a's diversity test (claims audit L6, review round; preregistered in claims-audit §6.1; $0).

E15a tested whether LLM-written guidance reduces the diversity of what a live agent explores: statistic =
mean pairwise Jaccard distance of the produced sets within the unguided arm (U) minus within the guided arm (G),
one-sided permutation test on the arm labels, 12 attempts per arm. This script estimates the power of that test
(alpha = 0.05) against a relative reduction f of the guided arm's pairwise distances, from the 24 recorded
attempts in ``results/dream-rsi/e15_guidance_live.json``. No LLM call is made.

* relabel (primary): each simulation splits the 24 observed attempts at random into two arms of 12 (the null
  holds by construction), multiplies the pseudo-G arm's pairwise distances by (1 - f) and runs the permutation
  test with 999 permutations;
* bootstrap (the reviewer's method): 12 U and 12 G attempts resampled with replacement from their own arms
  (duplicates have distance 0), G distances shrunk;
* shrink: the observed G arm's distances shrunk by f, one permutation test (10,000 permutations).

    python experiments/dream-rsi/e15_power.py [--sims 1000]
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import RESULTS, save  # noqa: E402
from e15_guidance_live import jaccard  # noqa: E402

FRACS = (0.05, 0.10, 0.15, 0.20)
ALPHA = 0.05


def stat_batch(D, masks):
    """masks: (P, n) booleans, True = G. Returns meanpair(U) - meanpair(G) for every row."""
    g = masks.astype(float)
    u = 1.0 - g
    ng, nu = g.sum(1), u.sum(1)
    sg = np.einsum("pi,ij,pj->p", g, D, g) / 2.0
    su = np.einsum("pi,ij,pj->p", u, D, u) / 2.0
    return su / (nu * (nu - 1) / 2) - sg / (ng * (ng - 1) / 2)


def perm_p(D, gmask, rng, nperm):
    n = len(gmask)
    obs = stat_batch(D, gmask[None, :])[0]
    perms = np.array([rng.permutation(gmask) for _ in range(nperm)])
    null = stat_batch(D, perms)
    return (1 + np.sum(null >= obs - 1e-15)) / (nperm + 1), obs


def shrink(D, gmask, f):
    D2 = D.copy()
    gi = np.where(gmask)[0]
    D2[np.ix_(gi, gi)] *= (1.0 - f)
    return D2


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sims", type=int, default=1000)
    ap.add_argument("--nperm", type=int, default=999)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    d = json.loads((RESULTS / "e15_guidance_live.json").read_text())
    att = d["attempts"]
    keys = sorted(att)
    sets = [att[k]["set"] if isinstance(att[k]["set"], list) else json.loads(att[k]["set"]) for k in keys]
    arms = np.array([att[k]["arm"] for k in keys])
    D = np.array([[jaccard(x, y) for y in sets] for x in sets])
    gmask = arms == "G"
    rng = np.random.default_rng(a.seed)
    p_obs, obs = perm_p(D, gmask, rng, 9999)
    out = {"observed": {"jaccard_U_minus_G": float(obs), "permutation_p": float(p_obs)}, "relabel": {},
           "bootstrap": {}, "shrink_observed": {}}
    n = len(keys)
    ui, gi = np.where(~gmask)[0], np.where(gmask)[0]
    for f in FRACS:
        hits = 0
        for _ in range(a.sims):
            m = np.zeros(n, bool)
            m[rng.choice(n, n // 2, replace=False)] = True
            p, _ = perm_p(shrink(D, m, f), m, rng, a.nperm)
            hits += p < ALPHA
        out["relabel"][str(f)] = hits / a.sims
        hits = 0
        for _ in range(a.sims):
            idx = np.concatenate([rng.choice(ui, 12), rng.choice(gi, 12)])
            m = np.array([False] * 12 + [True] * 12)
            p, _ = perm_p(shrink(D[np.ix_(idx, idx)], m, f), m, rng, a.nperm)
            hits += p < ALPHA
        out["bootstrap"][str(f)] = hits / a.sims
        out["shrink_observed"][str(f)] = float(perm_p(shrink(D, gmask, f), gmask, rng, 9999)[0])
        print(f"f={f:.2f}: power relabel {out['relabel'][str(f)]:.3f}, bootstrap {out['bootstrap'][str(f)]:.3f}; "
              f"observed-G shrunk p = {out['shrink_observed'][str(f)]:.4f}", flush=True)
    save("e15_power", {"config": {"sims": a.sims, "nperm": a.nperm, "alpha": ALPHA, "seed": a.seed,
                                  "fractions": FRACS, "source": "results/dream-rsi/e15_guidance_live.json",
                                  "statistic": "meanpair_Jaccard(U) - meanpair_Jaccard(G), one-sided"},
                       "results": out})


if __name__ == "__main__":
    main()
