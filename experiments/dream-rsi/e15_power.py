"""P1: power of E15a's diversity permutation test against a relative shrink f of the guided arm's distances ($0).

    python experiments/dream-rsi/e15_power.py [--sims 1000] [--nperm 999] [--seed 0]
"""
import argparse
import json

import numpy as np
from _common import RESULTS, save
from e15_guidance_live import jaccard

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
    obs = stat_batch(D, gmask[None, :])[0]
    null = stat_batch(D, np.array([rng.permutation(gmask) for _ in range(nperm)]))
    return (1 + np.sum(null >= obs - 1e-15)) / (nperm + 1), obs


def shrink(D, gmask, f):
    D2 = D.copy()
    gi = np.where(gmask)[0]
    D2[np.ix_(gi, gi)] *= (1.0 - f)
    return D2


def power(sample, f, rng, sims, nperm):
    """Share of ``sims`` simulated experiments (``sample() -> (D, gmask)``) whose test rejects at ALPHA."""
    hits = 0
    for _ in range(sims):
        D, m = sample()
        p, _ = perm_p(shrink(D, m, f), m, rng, nperm)
        hits += p < ALPHA
    return hits / sims


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sims", type=int, default=1000)
    ap.add_argument("--nperm", type=int, default=999)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    att = json.loads((RESULTS / "e15_guidance_live.json").read_text())["attempts"]
    keys = sorted(att)
    sets = [att[k]["set"] if isinstance(att[k]["set"], list) else json.loads(att[k]["set"]) for k in keys]
    D = np.array([[jaccard(x, y) for y in sets] for x in sets])
    gmask = np.array([att[k]["arm"] for k in keys]) == "G"
    rng = np.random.default_rng(a.seed)
    p_obs, obs = perm_p(D, gmask, rng, 9999)
    out = {"observed": {"jaccard_U_minus_G": float(obs), "permutation_p": float(p_obs)}, "relabel": {},
           "bootstrap": {}, "shrink_observed": {}}
    n = len(keys)
    ui, gi = np.where(~gmask)[0], np.where(gmask)[0]

    def relabel():
        """Primary: a random split of all attempts into two arms of n/2 (the null holds by construction)."""
        m = np.zeros(n, bool)
        m[rng.choice(n, n // 2, replace=False)] = True
        return D, m

    def bootstrap():
        """12 U and 12 G attempts resampled with replacement from their own arms."""
        idx = np.concatenate([rng.choice(ui, 12), rng.choice(gi, 12)])
        return D[np.ix_(idx, idx)], np.array([False] * 12 + [True] * 12)

    for f in FRACS:
        out["relabel"][str(f)] = power(relabel, f, rng, a.sims, a.nperm)
        out["bootstrap"][str(f)] = power(bootstrap, f, rng, a.sims, a.nperm)
        out["shrink_observed"][str(f)] = float(perm_p(shrink(D, gmask, f), gmask, rng, 9999)[0])
        print(f"f={f:.2f}: power relabel {out['relabel'][str(f)]:.3f}, bootstrap {out['bootstrap'][str(f)]:.3f}; "
              f"observed-G shrunk p = {out['shrink_observed'][str(f)]:.4f}", flush=True)
    save("e15_power", {"config": {"sims": a.sims, "nperm": a.nperm, "alpha": ALPHA, "seed": a.seed,
                                  "fractions": FRACS, "source": "results/dream-rsi/e15_guidance_live.json",
                                  "statistic": "meanpair_Jaccard(U) - meanpair_Jaccard(G), one-sided"},
                       "results": out})


if __name__ == "__main__":
    main()
