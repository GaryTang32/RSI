"""E10 - delta calibration is sound (about 97.5% clearance at z = 2).

For the unchanged base harness we draw a large pool of independent evaluations (k = 2, the
paper's default; plus a k = 4 pool). The TRUE null sd of S_hat is estimated from the whole
pool. Each calibration procedure is then applied to fresh subsets:

* within-task bootstrap of ONE evaluation (the code's default, reps = 2000, seed = 7);
* the same with the small-k correction sqrt(k/(k-1)) (extension, off by default);
* R = 2, 3, 5 repeated base evaluations (sd_null = stdev(S_r) * sqrt(2)).

Clearance = P(S_new >= S_base - delta) for two independent evaluations of the SAME
harness, measured on pool pairs not used for calibration (and analytically as
Phi(delta / sd_true)); z in {1, 2, 3}. Domains: HarnessWorld (5 worlds) and AgentQA with
SimModel (2 suites).

Confirming outcome (spec E10): about 97.5% clearance at z = 2; bootstrap and repeated
estimates agree within their spread.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np  # noqa: E402
from _common import RESULTS, parse_args, pmap, save  # noqa: E402
from scipy.stats import norm  # noqa: E402

from rsi.core import summarize_runs  # noqa: E402
from rsi.rrsi import Measurer  # noqa: E402
from rsi.rrsi.calibrate import calibrate  # noqa: E402

ZS = (1.0, 2.0, 3.0)


def make(domain_kind: str, seed: int):
    if domain_kind == "harnessworld":
        from rsi.domains.harnessworld import make_domain
        dom = make_domain(seed=seed)
        return dom, dom.seed_artifact(), None
    from rsi.domains.agentqa import AgentQADomain, SimModel, make_suite
    suite = make_suite(seed=seed)
    return AgentQADomain(suite), AgentQADomain.seed_artifact(), SimModel(suite)


def one(job: dict) -> dict:
    kind, seed, pool_n, cal_n = job["kind"], job["seed"], job["pool"], job["cal"]
    dom, art, llm = make(kind, seed)
    res = {"kind": kind, "seed": seed}
    for k in (2, 4):
        n = pool_n if k == 2 else pool_n // 2
        me = Measurer(dom, llm, workers=1, run_seed=1000 + seed, trace_chars=0)
        pool = [me.measure(art, f"pool{k}_{i}", k, keep_trials=False) for i in range(n)]
        S = np.array([p.S for p in pool])
        sd_true = float(np.std(S, ddof=1) * math.sqrt(2))
        rng = np.random.default_rng(seed)
        procs = {}
        # bootstrap of one evaluation (first cal_n evaluations), with and without the small-k correction
        boot = [calibrate([pool[i]], z=1.0, reps=2000, seed=7)["sd_null"] for i in range(cal_n)]
        procs["bootstrap (1 eval)"] = boot
        procs["bootstrap + small-k correction"] = [b * math.sqrt(k / (k - 1)) for b in boot]
        for R in (2, 3, 5):
            sds = []
            for _ in range(cal_n):
                idx = rng.choice(n, size=R, replace=False)
                sds.append(float(np.std(S[idx], ddof=1) * math.sqrt(2)))
            procs[f"repeats R={R}"] = sds
        # empirical clearance on pairs of pool evaluations
        pairs = rng.choice(n, size=(4000, 2))
        pairs = pairs[pairs[:, 0] != pairs[:, 1]]
        diffs = S[pairs[:, 1]] - S[pairs[:, 0]]            # S_new - S_base
        out = {"sd_true": sd_true, "S_mean": float(S.mean()), "n_pool": n, "procedures": {}}
        for name, sds in procs.items():
            sds = np.array(sds)
            row = {"sd_est_over_true": summarize_runs(list(sds / sd_true))}
            for z in ZS:
                deltas = z * sds
                analytic = norm.cdf(deltas / sd_true)
                empirical = [float(np.mean(diffs >= -d)) for d in deltas]
                row[f"z={z:g}"] = {"clearance_analytic": summarize_runs(list(analytic)),
                                   "clearance_empirical": summarize_runs(empirical),
                                   "delta": summarize_runs(list(deltas))}
            out["procedures"][name] = row
        res[f"k={k}"] = out
    return res


def main():
    a = parse_args("E10: noise-band calibration", default_seeds=5)
    pool, cal = (60, 20) if a.quick else (300, 100)
    jobs = [{"kind": "harnessworld", "seed": s, "pool": pool, "cal": cal} for s in range(a.seeds)]
    jobs += [{"kind": "agentqa", "seed": s, "pool": pool, "cal": cal} for s in range(min(2, a.seeds))]
    rows = pmap(one, jobs, a.workers)
    agg = {}
    for kind in ("harnessworld", "agentqa"):
        rs = [r for r in rows if r["kind"] == kind]
        if not rs:
            continue
        agg[kind] = {}
        for k in ("k=2", "k=4"):
            agg[kind][k] = {}
            for proc in rs[0][k]["procedures"]:
                agg[kind][k][proc] = {
                    "sd_est_over_true": float(np.mean([r[k]["procedures"][proc]["sd_est_over_true"]["mean"] for r in rs])),
                    **{f"z={z:g}": {"clearance_analytic": float(np.mean([r[k]["procedures"][proc][f"z={z:g}"]["clearance_analytic"]["mean"] for r in rs])),
                                    "clearance_empirical": float(np.mean([r[k]["procedures"][proc][f"z={z:g}"]["clearance_empirical"]["mean"] for r in rs]))}
                       for z in ZS}}
    target = norm.cdf(2.0)
    checks = {}
    for kind in agg:
        rep = agg[kind]["k=2"]["repeats R=5"]["z=2"]["clearance_empirical"]
        boot = agg[kind]["k=2"]["bootstrap (1 eval)"]["z=2"]["clearance_empirical"]
        corr = agg[kind]["k=2"]["bootstrap + small-k correction"]["z=2"]["clearance_empirical"]
        checks[f"{kind}: repeated evaluations (R=5) give ~97.5% at z=2"] = abs(rep - target) < 0.03
        checks[f"{kind}: code-default bootstrap (k=2) gives ~97.5% at z=2"] = abs(boot - target) < 0.03
        checks[f"{kind}: corrected bootstrap gives ~97.5% at z=2"] = abs(corr - target) < 0.03
    out = {"experiment": "E10 calibration", "config": {"pool_evals": pool, "calibrations": cal, "z": ZS,
                                                       "worlds": a.seeds, "agentqa_suites": min(2, a.seeds),
                                                       "llm": "none (SimModel / simulated policy)"},
           "target_clearance_z2": target, "aggregate": agg, "checks": checks,
           "verdict": "REPRODUCED" if all(checks.values()) else
           "PARTIAL - not met: " + "; ".join(k for k, v in checks.items() if not v),
           "rows": rows}
    save("e10_calibration", out)
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(1, len(agg), figsize=(7 * len(agg), 4.2), squeeze=False)
        for j, kind in enumerate(agg):
            procs = list(agg[kind]["k=2"])
            for i, z in enumerate(ZS):
                ax[0][j].bar([p + 0.25 * i for p in range(len(procs))],
                             [agg[kind]["k=2"][p][f"z={z:g}"]["clearance_empirical"] * 100 for p in procs], width=0.25,
                             label=f"z={z:g} (target {norm.cdf(z) * 100:.1f}%)")
            ax[0][j].axhline(target * 100, color="k", ls=":")
            ax[0][j].set_ylim(60, 100)
            ax[0][j].set_xticks([p + 0.25 for p in range(len(procs))])
            ax[0][j].set_xticklabels(procs, fontsize=7, rotation=15)
            ax[0][j].set_ylabel("% unchanged-harness re-evaluations clearing S - delta")
            ax[0][j].set_title(f"{kind} (k = 2)")
            ax[0][j].legend(fontsize=7)
        fig.tight_layout()
        fig.savefig(RESULTS / "e10_calibration.png", dpi=110)
    except Exception as e:  # noqa: BLE001
        print("figure skipped:", e)
    for kind in agg:
        for k in agg[kind]:
            for p, v in agg[kind][k].items():
                print(kind, k, p, round(v["sd_est_over_true"], 3), {z: round(v[z]["clearance_empirical"], 4) for z in v if z.startswith("z")})
    print(out["verdict"])


if __name__ == "__main__":
    main()
