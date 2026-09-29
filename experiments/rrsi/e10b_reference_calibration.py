"""E10b (retry round 2, preregistered P1 + P2 in docs/methods/rrsi/claims-audit.md section 6).

P1 (claim M37). The reference docstring says delta = z * sd(null dS) at z = 2 means "an unchanged
harness clears the floor S* - delta about 97.5% of the time". E10 tested this with OUR port of the
estimator; here the REFERENCE's own ``rrsi.calibrate.calibrate`` (google-research/rrsi@be50316,
Python ``random``, seed 7, 2,000 resamples) is imported and fed with the reference's
``EvalResult`` / ``TaskResult`` built from our trials. Paths:

* (i)   the driver's default ``calibrate --jobs base``: one base evaluation -> within-task bootstrap;
* (ii)  ``--jobs base,base2``: R = 2 repeated evaluations (sd_null = stdev * sqrt 2); also R = 3, 5.

Loop-faithful clearance: delta is calibrated from the base evaluation b itself, S* = S_b (round 0),
and S_new is another independent evaluation n of the same harness: clearance = P(S_n >= S_b - delta_b),
over 4,000 random ordered pairs (b among the calibrated evaluations). Also: the E10-style number
(delta and pairs drawn independently), two-sided coverage P(|dS| <= delta), and the analytic
prediction Phi(z * sqrt((k-1)/k)) for the plug-in bootstrap.

P2 (claim C19). (a) the R = 2 estimate's noisiness (CV of delta, clearance). (b) exchangeability: a
Monte-Carlo in which every evaluation carries a shared per-(evaluation, task) logit shock u ~ N(0, tau^2)
applied to all k trials of the task; delta = reference bootstrap of one evaluation WITH the
sqrt(k/(k-1)) small-k correction (isolating exchangeability from the plug-in bias).

Usage: python experiments/rrsi/e10b_reference_calibration.py [--ref-src PATH] [--workers 2] [--quick]
"""
from __future__ import annotations

import argparse
import importlib
import math
import os
import random
import statistics as st
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np  # noqa: E402
from _common import RESULTS, pmap, save  # noqa: E402
from scipy.stats import norm  # noqa: E402

from rsi.core import summarize_runs  # noqa: E402
from rsi.rrsi import Measurer  # noqa: E402

DEFAULT_REF = os.environ.get(
    "RRSI_REF_SRC",
    "/tmp/claude-0/-home-user-RSI/ebd00391-ba98-5b98-9125-83abd1dce979/scratchpad/src/google-research__rrsi")
TARGET = float(norm.cdf(2.0))
Z = 2.0


def _ref(ref_src: str):
    if ref_src not in sys.path:
        sys.path.insert(0, ref_src)
    RC = importlib.import_module("rrsi.calibrate")
    RE = importlib.import_module("rrsi.evaluate")
    return RC, RE


def to_ref(RE, ev, job: str):
    """Our Measurement -> the reference's EvalResult (same rewards, weights, tokens, missing)."""
    per = {t: RE.TaskResult(rewards=list(tr.rewards), weights=list(tr.weights), tokens=list(tr.tokens),
                            missing=int(tr.missing)) for t, tr in ev.per_task.items()}
    return RE.aggregate(job, ev.k, per)


def ref_delta(RC, evals: list, reps: int = 2000) -> dict:
    """The reference's calibrate(). For R >= 2 the bootstrap result is used only when sd_null == 0, so it is
    first run with 2 resamples and re-run with the full 2,000 only in that (degenerate) case: identical output."""
    if len(evals) >= 2:
        cal = RC.calibrate(evals, z=Z, reps=2)
        if cal["sd_null"] == 0 or len({e.S for e in evals}) == 1:
            cal = RC.calibrate(evals, z=Z, reps=reps)
        return cal
    return RC.calibrate(evals, z=Z, reps=reps)


def make(kind: str, seed: int):
    if kind == "harnessworld":
        from rsi.domains.harnessworld import make_domain
        dom = make_domain(seed=seed)
        return dom, dom.seed_artifact(), None
    from rsi.domains.agentqa import AgentQADomain, SimModel, make_suite
    suite = make_suite(seed=seed)
    return AgentQADomain(suite), AgentQADomain.seed_artifact(), SimModel(suite)


def clearance_stats(S: np.ndarray, base_idx: np.ndarray, new_idx: np.ndarray, delta_of_base: np.ndarray) -> dict:
    d = S[new_idx] - S[base_idx]
    return {"one_sided": float(np.mean(d >= -delta_of_base)), "two_sided": float(np.mean(np.abs(d) <= delta_of_base))}


# ------------------------------------------------------------------------------------------------ P1 / P2a
def p1_job(job: dict) -> dict:
    RC, RE = _ref(job["ref"])
    kind, seed, pool_n, cal_n = job["kind"], job["seed"], job["pool"], job["cal"]
    dom, art, llm = make(kind, seed)
    out = {"kind": kind, "seed": seed}
    for k in (2, 4):
        n = pool_n if k == 2 else pool_n // 2
        me = Measurer(dom, llm, workers=1, run_seed=1000 + seed, trace_chars=0)      # the E10 pools, bit for bit
        pool = [me.measure(art, f"pool{k}_{i}", k, keep_trials=False) for i in range(n)]
        refs = [to_ref(RE, p, f"pool{k}_{i}") for i, p in enumerate(pool)]
        S = np.array([r.S for r in refs])
        assert np.allclose(S, [p.S for p in pool]), "reference aggregate differs from ours"
        sd_true = float(np.std(S, ddof=1) * math.sqrt(2))
        rng = np.random.default_rng(seed)
        res = {"sd_true": sd_true, "n_pool": n, "S_mean": float(S.mean()), "procedures": {}}
        # (i) code default: one base evaluation, reference bootstrap
        t0 = time.time()
        boot = np.array([ref_delta(RC, [refs[i]])["delta"] for i in range(cal_n)])
        b = rng.integers(0, cal_n, 4000)
        nn = rng.integers(0, n, 4000)
        keep = b != nn
        b, nn = b[keep], nn[keep]
        pb, pn = _indep_pairs(rng, n)
        row = {"delta_over_2sd_true": summarize_runs(list(boot / (Z * sd_true))),
               "loop_faithful": clearance_stats(S, b, nn, boot[b]),
               "e10_style": clearance_stats(S, pb, pn, rng.choice(boot, len(pb))),
               "analytic_plugin": float(norm.cdf(Z * math.sqrt((k - 1) / k))), "seconds": round(time.time() - t0, 1)}
        corr = boot * math.sqrt(k / (k - 1))
        row_c = {"delta_over_2sd_true": summarize_runs(list(corr / (Z * sd_true))),
                 "loop_faithful": clearance_stats(S, b, nn, corr[b])}
        res["procedures"]["(i) reference bootstrap, 1 eval (code default)"] = row
        res["procedures"]["(i') same + sqrt(k/(k-1)) correction (our opt-in)"] = row_c
        # (ii) R repeated evaluations: base = the first of the R evaluations (the run's baseline job)
        for R in (2, 3, 5):
            deltas, clear1, clear2 = [], [], []
            for _ in range(cal_n):
                idx = rng.choice(n, size=R + 1, replace=False)          # R calibration evals + 1 fresh eval
                cal = ref_delta(RC, [refs[i] for i in idx[:R]])
                dlt = cal["delta"]
                deltas.append(dlt)
                d = S[idx[R]] - S[idx[0]]
                clear1.append(float(d >= -dlt))
                clear2.append(float(abs(d) <= dlt))
            deltas = np.array(deltas)
            res["procedures"][f"(ii) reference repeats R={R}"] = {
                "delta_over_2sd_true": summarize_runs(list(deltas / (Z * sd_true))),
                "delta_cv": float(np.std(deltas, ddof=1) / np.mean(deltas)) if np.mean(deltas) > 0 else float("nan"),
                "loop_faithful": {"one_sided": float(np.mean(clear1)), "two_sided": float(np.mean(clear2))}}
        out[f"k={k}"] = res
    return out


def _indep_pairs(rng, n):
    p = rng.integers(0, n, size=(4000, 2))
    p = p[p[:, 0] != p[:, 1]]
    return p[:, 0], p[:, 1]


# ------------------------------------------------------------------------------------------------ P2b
def _sim_eval(RE, logit_p: np.ndarray, tau: float, k: int, n_crit: int, rng: np.random.Generator, job: str):
    u = rng.normal(0.0, tau, size=len(logit_p)) if tau > 0 else np.zeros(len(logit_p))
    p = 1.0 / (1.0 + np.exp(-(logit_p + u)))
    passed = rng.binomial(n_crit, np.repeat(p[:, None], k, axis=1))
    per = {f"t{i}": RE.TaskResult(rewards=[float(x) / n_crit for x in passed[i]]) for i in range(len(p))}
    return RE.aggregate(job, k, per)


def p2b_job(job: dict) -> dict:
    RC, RE = _ref(job["ref"])
    from rsi.domains.harnessworld import make_domain
    seed, tau, n_cal, n_pool = job["seed"], job["tau"], job["cal"], job["pool"]
    dom = make_domain(seed=seed)
    p_map = dom._state(dom.seed_artifact())[0]
    ids = dom.tasks.splits["evolve"]
    pv = np.clip(np.array([p_map[i] for i in ids]), 1e-6, 1 - 1e-6)
    logit_p = np.log(pv / (1 - pv))
    k, n_crit = 2, dom.world.cfg.n_criteria
    rng = np.random.default_rng(10_000 + seed * 10 + int(tau * 100))
    pool = [_sim_eval(RE, logit_p, tau, k, n_crit, rng, f"sim{i}") for i in range(n_pool)]
    S = np.array([e.S for e in pool])
    sd_true = float(np.std(S, ddof=1) * math.sqrt(2))
    raw = np.array([RC.calibrate([pool[i]], z=Z)["delta"] for i in range(n_cal)])
    corr = raw * math.sqrt(k / (k - 1))
    b = rng.integers(0, n_cal, 4000)
    nn = rng.integers(0, n_pool, 4000)
    keep = b != nn
    b, nn = b[keep], nn[keep]
    return {"seed": seed, "tau": tau, "sd_true": sd_true,
            "corrected": {"delta_over_2sd_true": float(np.mean(corr) / (Z * sd_true)),
                          **clearance_stats(S, b, nn, corr[b])},
            "uncorrected": {"delta_over_2sd_true": float(np.mean(raw) / (Z * sd_true)),
                            **clearance_stats(S, b, nn, raw[b])}}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--ref-src", default=DEFAULT_REF)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--quick", action="store_true")
    a = ap.parse_args()
    pool, cal = (60, 20) if a.quick else (300, 100)
    RC, _ = _ref(a.ref_src)
    t0 = time.time()
    jobs = [{"kind": "harnessworld", "seed": s, "pool": pool, "cal": cal, "ref": a.ref_src} for s in range(5)]
    jobs += [{"kind": "agentqa", "seed": s, "pool": pool, "cal": cal, "ref": a.ref_src} for s in range(2)]
    taus = (0.0, 0.25, 0.5)
    jobs2 = [{"seed": s, "tau": t, "cal": 40 if a.quick else 200, "pool": 100 if a.quick else 400, "ref": a.ref_src}
             for s in range(5) for t in taus]
    rows = pmap(p1_job, jobs, a.workers)
    rows2 = pmap(p2b_job, jobs2, a.workers)
    agg: dict = {}
    for kind in ("harnessworld", "agentqa"):
        rs = [r for r in rows if r["kind"] == kind]
        agg[kind] = {}
        for k in ("k=2", "k=4"):
            agg[kind][k] = {}
            for proc in rs[0][k]["procedures"]:
                v = [r[k]["procedures"][proc] for r in rs]
                agg[kind][k][proc] = {
                    "clearance_one_sided": float(np.mean([x["loop_faithful"]["one_sided"] for x in v])),
                    "coverage_two_sided": float(np.mean([x["loop_faithful"]["two_sided"] for x in v])),
                    "delta_over_2sd_true": float(np.mean([x["delta_over_2sd_true"]["mean"] for x in v])),
                    **({"clearance_e10_style": float(np.mean([x["e10_style"]["one_sided"] for x in v]))}
                       if "e10_style" in v[0] else {}),
                    **({"delta_cv": float(np.mean([x["delta_cv"] for x in v]))} if "delta_cv" in v[0] else {}),
                    **({"analytic_plugin": v[0]["analytic_plugin"]} if "analytic_plugin" in v[0] else {})}
    exch = {}
    for t in taus:
        rs = [r for r in rows2 if r["tau"] == t]
        exch[f"tau={t:g}"] = {c: {m: float(np.mean([r[c][m] for r in rs]))
                                  for m in ("one_sided", "two_sided", "delta_over_2sd_true")}
                              for c in ("corrected", "uncorrected")}
    code_default = {kind: agg[kind]["k=2"]["(i) reference bootstrap, 1 eval (code default)"]["clearance_one_sided"]
                    for kind in agg}
    r2 = {kind: agg[kind]["k=2"]["(ii) reference repeats R=2"] for kind in agg}
    checks = {
        "P1 pass (reference code default, k=2, |clearance - Phi(2)| < 3 pts), harnessworld":
            abs(code_default["harnessworld"] - TARGET) < 0.03,
        "P1 pass (reference code default, k=2, |clearance - Phi(2)| < 3 pts), agentqa":
            abs(code_default["agentqa"] - TARGET) < 0.03,
        "P2a confirmed (R=2: delta CV >= 0.5 and clearance < Phi(2) - 3 pts), harnessworld":
            r2["harnessworld"]["delta_cv"] >= 0.5 and r2["harnessworld"]["clearance_one_sided"] < TARGET - 0.03,
        "P2a confirmed (R=2: delta CV >= 0.5 and clearance < Phi(2) - 3 pts), agentqa":
            r2["agentqa"]["delta_cv"] >= 0.5 and r2["agentqa"]["clearance_one_sided"] < TARGET - 0.03,
        "P2b confirmed (tau=0.5 corrected clearance >= 3 pts below tau=0 and < Phi(2) - 3 pts)":
            (exch["tau=0"]["corrected"]["one_sided"] - exch["tau=0.5"]["corrected"]["one_sided"] >= 0.03
             and exch["tau=0.5"]["corrected"]["one_sided"] < TARGET - 0.03),
    }
    out = {"experiment": "E10b reference-exact calibration (retry round 2: P1, P2)",
           "reference": {"src": a.ref_src, "commit": "be50316", "calibrate_module": RC.__file__},
           "config": {"pool_evals_k2": pool, "pool_evals_k4": pool // 2, "calibrations": cal, "z": Z,
                      "pairs": 4000, "hw_worlds": 5, "agentqa_suites": 2, "taus": taus},
           "target_clearance_z2": TARGET, "aggregate": agg, "exchangeability": exch, "checks": checks,
           "wall_s": round(time.time() - t0, 1), "rows": rows, "rows_exchangeability": rows2}
    save("e10b_reference_calibration", out)
    for kind in agg:
        for k in agg[kind]:
            for p, v in agg[kind][k].items():
                print(kind, k, p, {m: round(x, 4) for m, x in v.items()})
    for t, v in exch.items():
        print(t, v)
    for c, v in checks.items():
        print(("PASS " if v else "FAIL ") + c)


if __name__ == "__main__":
    main()
