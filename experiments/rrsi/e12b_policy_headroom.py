"""E12b (retry round 2, preregistered P3 in docs/methods/rrsi/claims-audit.md section 6): claim L12.

"A weaker search policy gets a larger evolve gain than a strong one" (Table 3/4: Gemini 3.5 Flash
64.6 -> 78.7, +14.1, vs Claude Opus 4.8 74.2 -> 80.2, +6.0, both on Terminal-Bench 2.1, both
evolved separately with the same Opus proposer). The paper's starting levels are both above 50%;
default HarnessWorld's STRONG / WEAK start near 0.4 / 0.2 and E12's WEAK also shrinks most
per-mechanism effects, so the earlier analogue (policy_swap) could not show a headroom effect.

Here the frozen search policies are set to the paper's H_0 levels on the evolve set:

* STRONG_P / WEAK_P: every component scale 1.0 (neutral), strength by bisection so that the mean
  analytic H_0 evolve E[S] over calibration worlds 1000-1019 (never used below) is 0.742 / 0.646;
* WEAK_P_E12 (secondary): E12's WEAK component scales, strength calibrated to 0.646 the same way.

Full RRSI, coding preset (Table 3/4's domain) with delta calibrated per run, T = 20, worlds 0-99,
each world run under all three policies (paired by world). Metric: true evolve gain under the run's
own policy. Pass: paired (WEAK_P - STRONG_P) CI lower bound > 0 (primary); secondary the same for
WEAK_P_E12.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np  # noqa: E402
from _common import analyze_hw, paired, pmap, save, search_llm, strip_curves, summarize, switches  # noqa: E402

from rsi.domains.harnessworld import WEAK, make_domain  # noqa: E402
from rsi.domains.harnessworld.world import Policy, World, WorldConfig  # noqa: E402
from rsi.rrsi import Config  # noqa: E402
from rsi.rrsi.config import PRESETS  # noqa: E402

TARGETS = {"strong_p": 0.742, "weak_p": 0.646, "weak_p_e12": 0.646}
SCALES = {"strong_p": {}, "weak_p": {}, "weak_p_e12": dict(WEAK.component_scale)}
CAL_WORLDS = range(1000, 1020)


_WORLDS: dict = {}


def mean_h0(strength: float, scale: dict, worlds) -> float:
    vals = []
    for w in worlds:
        world = _WORLDS.get(w) or _WORLDS.setdefault(w, World(WorldConfig(seed=w)))
        vals.append(world.expected(world.seed_files(), "evolve", Policy("cal", strength, scale))["S"])
    return float(np.mean(vals))


def calibrate_strength(target: float, scale: dict, worlds=CAL_WORLDS, tol: float = 1e-5) -> float:
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


def one(job: dict) -> dict:
    seed, name, strength = job["seed"], job["policy"], job["strength"]
    pol = Policy(name, strength, SCALES[name])
    dom = make_domain(seed=seed, policy=pol)
    llm = search_llm("sim", dom.world)
    cfg = dict(PRESETS["coding"])
    cfg.update(delta=None, workers=1, seed=seed, T=job["T"])
    out = Path(tempfile.mkdtemp(prefix=f"e12b_{name}_{seed}_", dir=job["scratch"]))
    t0 = time.time()
    from rsi.rrsi import run
    res = run(dom, dom.seed_artifact(), llm_propose=llm, config=Config(**cfg), out_dir=out, switches=switches("full"))
    if res.stop_reason != "max_rounds":
        raise RuntimeError(f"run {name} seed {seed} stopped early: {res.stop_reason}")
    m = analyze_hw(dom, out, res)
    m.pop("curve", None)
    m.update(seed=seed, policy=name, label=name, strength=strength, wall_s=time.time() - t0,
             analyst_mode=res.meta.get("analyst_mode"))
    shutil.rmtree(out, ignore_errors=True)
    return m


def main():
    ap = argparse.ArgumentParser(description="E12b: weak vs strong search policy at the paper's starting levels")
    ap.add_argument("--seeds", type=int, default=100)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--T", type=int, default=20)
    a = ap.parse_args()
    scratch = Path(os.environ.get("RRSI_SCRATCH", tempfile.gettempdir())) / "rrsi_runs"
    scratch.mkdir(parents=True, exist_ok=True)
    strengths = {n: calibrate_strength(TARGETS[n], SCALES[n]) for n in TARGETS}
    cal_check = {n: mean_h0(strengths[n], SCALES[n], CAL_WORLDS) for n in TARGETS}
    print("calibrated strengths", strengths, cal_check, flush=True)
    jobs = [{"seed": s, "policy": n, "strength": strengths[n], "T": a.T, "scratch": str(scratch)}
            for s in range(a.seeds) for n in TARGETS]
    rows = pmap(one, jobs, a.workers)
    metrics = ("evolve_H0", "evolve_gain", "measured_gain", "holdout_gain", "ood_gain", "token_ratio", "delta",
               "n_accepted", "n_mechanisms")
    summ = summarize(rows, metrics=metrics)
    pd = {f"{w} - strong_p": {m: paired(rows, "strong_p", w, m) for m in ("evolve_gain", "measured_gain", "ood_gain",
                                                                              "holdout_gain")}
          for w in ("weak_p", "weak_p_e12")}
    checks = {"primary: weak_p - strong_p true evolve gain CI > 0": pd["weak_p - strong_p"]["evolve_gain"]["lo"] > 0,
              "secondary: weak_p_e12 - strong_p true evolve gain CI > 0":
                  pd["weak_p_e12 - strong_p"]["evolve_gain"]["lo"] > 0}
    if checks["primary: weak_p - strong_p true evolve gain CI > 0"]:
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
        print(k, {m: (round(x["mean_diff"] * 100, 2), round(x["lo"] * 100, 2), round(x["hi"] * 100, 2))
                  for m, x in v.items()})
    print(checks, verdict)


if __name__ == "__main__":
    main()
