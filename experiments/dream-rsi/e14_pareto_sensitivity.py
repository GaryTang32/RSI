"""E14 - How much do the unpublished parts of the Listing-2 objective matter? (claims audit M18, retry round 2)

``pareto.reward = pareto.auc - lambda * parallel_penalty`` [paper:App.B.2 L2:12-22]: the published paper defines
neither the AUC, nor lambda, nor the beta grid. The arXiv source's unpublished method draft
(``Main_Text/method.tex``, commented out of ``main.tex``) adds one piece: attainment a = clip(S/G, 0, 1) (ours:
(S - root)/(G - root)). This replays a policy pool once per beta in {0.1, ..., 1.0} and recomputes the reward
under 12 variants: lambda in {0.05, 0.1, 0.2} x attainment in {shift, ratio} x grid in {0.2..1.0 step 0.2,
0.1..0.9 step 0.1}. Preregistered: "robust" iff min Spearman with the default >= 0.8 and the same top-1 policy in
>= 9 of 12 variants, per domain (M18 stays PARTIAL either way).

    python experiments/dream-rsi/e14_pareto_sensitivity.py
"""
import itertools

import numpy as np

from _common import parse_args, save, spearman

from e12_real_offpolicy import record as record_real
from e13_thousands import candidates, worlds as synthetic_worlds
from e2_offpolicy_validity import zoo
from rsi.dream import Eq1Objective, ParetoSweepObjective, ReplayEvaluator

BETAS = tuple(round(0.1 * i, 1) for i in range(1, 11))
GRIDS = {"0.2..1.0": (0.2, 0.4, 0.6, 0.8, 1.0), "0.1..0.9": (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)}
LAMS = (0.05, 0.1, 0.2)
MODES = ("shift", "ratio")
DEFAULT = (0.1, "shift", "0.2..1.0")


def episodes(policies, ws, W, fallback):
    ev = ReplayEvaluator(Eq1Objective(), W=W, fallback=fallback, runner="inprocess", hard_max=(12, 12))
    out = {}
    for name, code in policies.items():
        out[name] = {b: ev.evaluate(code, ws, config={"beta": b}, sweep=False).episodes for b in BETAS}
    return out


def variants(eps):
    res = {}
    for lam, mode, g in itertools.product(LAMS, MODES, GRIDS):
        obj = ParetoSweepObjective(beta_grid=GRIDS[g], lam=lam, attainment_mode=mode)
        res[(lam, mode, g)] = {n: obj.sweep({b: e[b] for b in GRIDS[g]})["reward"] for n, e in eps.items()}
    return res


def compare(res):
    names = list(res[DEFAULT])
    base = [res[DEFAULT][n] for n in names]
    top = max(names, key=lambda n: res[DEFAULT][n])
    rows = []
    for k, v in res.items():
        vals = [v[n] for n in names]
        rows.append({"lambda": k[0], "attainment": k[1], "grid": k[2], "spearman_vs_default": spearman(base, vals),
                     "top1": max(names, key=lambda n: v[n]), "same_top1": max(names, key=lambda n: v[n]) == top})
    rhos = [r["spearman_vs_default"] for r in rows]
    same = sum(r["same_top1"] for r in rows)
    by_mode = {m: float(np.min([r["spearman_vs_default"] for r in rows if r["attainment"] == m])) for m in MODES}
    by_lam = {str(l): float(np.min([r["spearman_vs_default"] for r in rows if r["lambda"] == l])) for l in LAMS}
    return {"variants": rows, "min_spearman": float(min(rhos)), "same_top1": f"{same}/{len(rows)}",
            "default_top1": top, "min_spearman_by_attainment": by_mode, "min_spearman_by_lambda": by_lam,
            "robust": bool(min(rhos) >= 0.8 and same >= 9)}


def main():
    parse_args("E14 Pareto-objective sensitivity", default_seeds=0)
    pol = dict(zoo())
    pol.update({f"cand_{i}": c for i, c in enumerate(candidates(40))})
    out = {}
    for dom, ws, W, fb in (("synthetic", synthetic_worlds(), 10, (10, 10)),
                           ("sumdiff", [record_real(("sumdiff", 100 + i)) for i in range(5)], 6, (6, 4))):
        eps = episodes(pol, ws, W, fb)
        out[dom] = compare(variants(eps))
        o = out[dom]
        print(f"[{dom}] min Spearman {o['min_spearman']:.3f} (by attainment {o['min_spearman_by_attainment']}, by "
              f"lambda {o['min_spearman_by_lambda']}); same top-1 {o['same_top1']}; robust {o['robust']}", flush=True)
    save("e14_pareto_sensitivity", {"config": {"betas_replayed": BETAS, "grids": GRIDS, "lambdas": LAMS,
                                               "attainment": {"shift": "(S - root)/(G - root) (ours)",
                                                              "ratio": "clip(S/G, 0, 1) (unpublished draft)"},
                                               "default": DEFAULT, "policies": list(pol)},
                                    "results": out})


if __name__ == "__main__":
    main()
