"""E10: delta calibration is sound (~97.5% clearance at z = 2): bootstrap and repeated-evaluation estimates vs truth.

Run: python experiments/rrsi/e10_calibration.py [--quick] [--seeds N]
"""
import math

import numpy as np
from _common import figure, parse_args, pmap, save, verdict
from scipy.stats import norm

from rsi.core import summarize_runs
from rsi.rrsi import Measurer
from rsi.rrsi.calibrate import calibrate

ZS = (1.0, 2.0, 3.0)


def make(kind: str, seed: int) -> tuple:
    """(domain, seed artifact, task LLM) of a HarnessWorld world or an AgentQA suite."""
    if kind == "harnessworld":
        from rsi.domains.harnessworld import make_domain
        dom = make_domain(seed=seed)
        return dom, dom.seed_artifact(), None
    from rsi.domains.agentqa import AgentQADomain, SimModel, make_suite
    suite = make_suite(seed=seed)
    return AgentQADomain(suite), AgentQADomain.seed_artifact(), SimModel(suite)


def procedure_row(sds: np.ndarray, sd_true: float, diffs: np.ndarray) -> dict:
    row = {"sd_est_over_true": summarize_runs(list(sds / sd_true))}
    for z in ZS:
        deltas = z * sds
        row[f"z={z:g}"] = {"clearance_analytic": summarize_runs(list(norm.cdf(deltas / sd_true))),
                           "clearance_empirical": summarize_runs([float(np.mean(diffs >= -d)) for d in deltas]),
                           "delta": summarize_runs(list(deltas))}
    return row


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
        boot = [calibrate([pool[i]], z=1.0, reps=2000, seed=7)["sd_null"] for i in range(cal_n)]
        procs = {"bootstrap (1 eval)": boot,
                 "bootstrap + small-k correction": [b * math.sqrt(k / (k - 1)) for b in boot]}
        for R in (2, 3, 5):
            procs[f"repeats R={R}"] = [float(np.std(S[rng.choice(n, size=R, replace=False)], ddof=1) * math.sqrt(2))
                                       for _ in range(cal_n)]
        pairs = rng.choice(n, size=(4000, 2))
        pairs = pairs[pairs[:, 0] != pairs[:, 1]]
        diffs = S[pairs[:, 1]] - S[pairs[:, 0]]            # S_new - S_base
        res[f"k={k}"] = {"sd_true": sd_true, "S_mean": float(S.mean()), "n_pool": n,
                         "procedures": {name: procedure_row(np.array(sds), sd_true, diffs)
                                        for name, sds in procs.items()}}
    return res


def aggregate(rs: list, k: str, proc: str) -> dict:
    def mean(get) -> float:
        return float(np.mean([get(r[k]["procedures"][proc]) for r in rs]))
    return {"sd_est_over_true": mean(lambda p: p["sd_est_over_true"]["mean"]),
            **{f"z={z:g}": {"clearance_analytic": mean(lambda p: p[f"z={z:g}"]["clearance_analytic"]["mean"]),
                            "clearance_empirical": mean(lambda p: p[f"z={z:g}"]["clearance_empirical"]["mean"])}
               for z in ZS}}


def main():
    a = parse_args("E10: noise-band calibration", default_seeds=5)
    pool, cal = (60, 20) if a.quick else (300, 100)
    jobs = [{"kind": "harnessworld", "seed": s, "pool": pool, "cal": cal} for s in range(a.seeds)]
    jobs += [{"kind": "agentqa", "seed": s, "pool": pool, "cal": cal} for s in range(min(2, a.seeds))]
    rows = pmap(one, jobs, a.workers)
    agg = {}
    for kind in ("harnessworld", "agentqa"):
        rs = [r for r in rows if r["kind"] == kind]
        if rs:
            agg[kind] = {k: {proc: aggregate(rs, k, proc) for proc in rs[0][k]["procedures"]} for k in ("k=2", "k=4")}
    target = norm.cdf(2.0)
    checks = {}
    for kind in agg:
        z2 = {p: v["z=2"]["clearance_empirical"] for p, v in agg[kind]["k=2"].items()}
        checks[f"{kind}: repeated evaluations (R=5) give ~97.5% at z=2"] = abs(z2["repeats R=5"] - target) < 0.03
        checks[f"{kind}: code-default bootstrap (k=2) gives ~97.5% at z=2"] = \
            abs(z2["bootstrap (1 eval)"] - target) < 0.03
        checks[f"{kind}: corrected bootstrap gives ~97.5% at z=2"] = \
            abs(z2["bootstrap + small-k correction"] - target) < 0.03
    out = {"experiment": "E10 calibration", "config": {"pool_evals": pool, "calibrations": cal, "z": ZS,
                                                       "worlds": a.seeds, "agentqa_suites": min(2, a.seeds),
                                                       "llm": "none (SimModel / simulated policy)"},
           "target_clearance_z2": target, "aggregate": agg, "checks": checks, "verdict": verdict(checks),
           "rows": rows}
    save("e10_calibration", out)

    def draw(ax):
        for j, kind in enumerate(agg):
            procs = list(agg[kind]["k=2"])
            x = ax[0][j]
            for i, z in enumerate(ZS):
                x.bar([p + 0.25 * i for p in range(len(procs))],
                      [agg[kind]["k=2"][p][f"z={z:g}"]["clearance_empirical"] * 100 for p in procs], width=0.25,
                      label=f"z={z:g} (target {norm.cdf(z) * 100:.1f}%)")
            x.axhline(target * 100, color="k", ls=":")
            x.set_ylim(60, 100)
            x.set_xticks([p + 0.25 for p in range(len(procs))])
            x.set_xticklabels(procs, fontsize=7, rotation=15)
            x.set_ylabel("% unchanged-harness re-evaluations clearing S - delta")
            x.set_title(f"{kind} (k = 2)")
            x.legend(fontsize=7)
    figure("e10_calibration", draw, len(agg), figsize=(7 * len(agg), 4.2), squeeze=False)
    for kind in agg:
        for k in agg[kind]:
            for p, v in agg[kind][k].items():
                print(kind, k, p, round(v["sd_est_over_true"], 3),
                      {z: round(v[z]["clearance_empirical"], 4) for z in v if z.startswith("z")})
    print(out["verdict"])


if __name__ == "__main__":
    main()
