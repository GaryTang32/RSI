"""E12b (preregistered P3, claim L12): does a weaker search policy get a larger evolve gain at the paper's H_0 levels?

Run: python experiments/rrsi/e12b_policy_headroom.py [--seeds 100] [--workers 2] [--T 20]
"""
import shutil
import tempfile
import time
from pathlib import Path

import numpy as np
from _common import (analyze_hw, ci_pts, pairs, pmap, retry_args, save, scratch_dir, search_llm, strip_curves,
                     summarize, switches)

from rsi.domains.harnessworld import WEAK, make_domain
from rsi.domains.harnessworld.world import Policy, World, WorldConfig
from rsi.rrsi import Config, run
from rsi.rrsi.config import PRESETS

# mean analytic H_0 evolve E[S] each policy is calibrated to (Table 3/4's starting levels)
TARGETS = {"strong_p": 0.742, "weak_p": 0.646, "weak_p_e12": 0.646}
SCALES = {"strong_p": {}, "weak_p": {}, "weak_p_e12": dict(WEAK.component_scale)}
CAL_WORLDS = range(1000, 1020)                   # never used by the runs
_WORLDS: dict = {}


def mean_h0(strength: float, scale: dict, worlds) -> float:
    vals = []
    for w in worlds:
        world = _WORLDS.get(w) or _WORLDS.setdefault(w, World(WorldConfig(seed=w)))
        vals.append(world.expected(world.seed_files(), "evolve", Policy("cal", strength, scale))["S"])
    return float(np.mean(vals))


def calibrate_strength(target: float, scale: dict, worlds=CAL_WORLDS, tol: float = 1e-5) -> float:
    """Bisection on the policy strength until mean_h0 hits target."""
    lo, hi = -4.0, 4.0
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if mean_h0(mid, scale, worlds) < target:
            lo = mid
        else:
            hi = mid
        if hi - lo < tol:
            break
    return round(0.5 * (lo + hi), 5)


def coding_config(seed: int, T: int) -> Config:
    """The coding preset with delta calibrated per run."""
    return Config(**{**PRESETS["coding"], "delta": None, "workers": 1, "seed": seed, "T": T})


def one(job: dict) -> dict:
    seed, name, strength = job["seed"], job["policy"], job["strength"]
    dom = make_domain(seed=seed, policy=Policy(name, strength, SCALES[name]))
    out = Path(tempfile.mkdtemp(prefix=f"e12b_{name}_{seed}_", dir=scratch_dir()))
    t0 = time.time()
    res = run(dom, dom.seed_artifact(), llm_propose=search_llm("sim", dom.world), config=coding_config(seed, job["T"]),
              out_dir=out, switches=switches("full"))
    if res.stop_reason != "max_rounds":
        raise RuntimeError(f"run {name} seed {seed} stopped early: {res.stop_reason}")
    m = analyze_hw(dom, out, res)
    m.pop("curve", None)
    m.update(seed=seed, policy=name, label=name, strength=strength, wall_s=time.time() - t0,
             analyst_mode=res.meta.get("analyst_mode"))
    shutil.rmtree(out, ignore_errors=True)
    return m


def main():
    a = retry_args("E12b: weak vs strong search policy at the paper's starting levels")
    strengths = {n: calibrate_strength(TARGETS[n], SCALES[n]) for n in TARGETS}
    cal_check = {n: mean_h0(strengths[n], SCALES[n], CAL_WORLDS) for n in TARGETS}
    print("calibrated strengths", strengths, cal_check, flush=True)
    jobs = [{"seed": s, "policy": n, "strength": strengths[n], "T": a.T} for s in range(a.seeds) for n in TARGETS]
    rows = pmap(one, jobs, a.workers)
    metrics = ("evolve_H0", "evolve_gain", "measured_gain", "holdout_gain", "ood_gain", "token_ratio", "delta",
               "n_accepted", "n_mechanisms")
    summ = summarize(rows, metrics=metrics)
    pd = {f"{w} - strong_p": pairs(rows, "strong_p", w, ("evolve_gain", "measured_gain", "ood_gain", "holdout_gain"))
          for w in ("weak_p", "weak_p_e12")}
    primary = "primary: weak_p - strong_p true evolve gain CI > 0"
    checks = {primary: pd["weak_p - strong_p"]["evolve_gain"]["lo"] > 0,
              "secondary: weak_p_e12 - strong_p true evolve gain CI > 0":
                  pd["weak_p_e12 - strong_p"]["evolve_gain"]["lo"] > 0}
    if checks[primary]:
        verdict = "REPRODUCED (HW analogue)" if all(checks.values()) else "PARTIAL"
    else:
        verdict = "NOT REPRODUCED"
    out = {"experiment": "E12b weak vs strong search policy at the paper's H_0 levels (retry round 2: P3)",
           "config": {"seeds": a.seeds, "T": a.T, "preset": "coding (delta calibrated per run)", "targets": TARGETS,
                      "calibration_worlds": [min(CAL_WORLDS), max(CAL_WORLDS)], "strengths": strengths,
                      "calibrated_mean_H0": cal_check, "component_scales": SCALES},
           "summary": summ, "paired": pd, "checks": checks, "verdict": verdict, "rows": strip_curves(rows)}
    save("e12b_policy_headroom", out)
    for lab, s in summ.items():
        print(lab, {m: round(s[m]["mean"], 4) for m in metrics})
    for k, v in pd.items():
        print(k, {m: ci_pts(x) for m, x in v.items()})
    print(checks, verdict)


if __name__ == "__main__":
    main()
