"""E12c (preregistered P3b, claim L12): split E12b's weak - strong gain into logistic-link curvature and search parts.

Run after E12b: python experiments/rrsi/e12c_curvature_split.py [--seeds 100] [--workers 2] [--T 20]
"""
import json
import shutil
import tempfile
import time
from pathlib import Path

import numpy as np
from _common import RESULTS, pmap, retry_args, save, scratch_dir, search_llm, switches
from e12b_policy_headroom import SCALES, coding_config

from rsi.core.ledger import ArtifactStore
from rsi.core.stats import bootstrap_ci
from rsi.domains.harnessworld import make_domain
from rsi.domains.harnessworld.world import Policy
from rsi.rrsi import run

E12B = RESULTS / "e12b_policy_headroom.json"
POLS = ("strong_p", "weak_p")


def one(job: dict) -> dict:
    """Evolve under one policy, then score the final harness's true evolve gain under both policies."""
    seed, name, strengths = job["seed"], job["policy"], job["strengths"]
    pols = {n: Policy(n, strengths[n], SCALES[n]) for n in POLS}
    dom = make_domain(seed=seed, policy=pols[name])
    out = Path(tempfile.mkdtemp(prefix=f"e12c_{name}_{seed}_", dir=scratch_dir()))
    t0 = time.time()
    res = run(dom, dom.seed_artifact(), llm_propose=search_llm("sim", dom.world), config=coding_config(seed, job["T"]),
              out_dir=out, switches=switches("full"))
    if res.stop_reason != "max_rounds":
        raise RuntimeError(f"run {name} seed {seed} stopped early: {res.stop_reason}")
    fr = json.loads((out / "frontier.json").read_text())
    st = ArtifactStore(out / "artifacts")
    h0, fin = st.get(fr["trajectory"][0]["artifact_id"]), st.get(fr["incumbent"]["artifact_id"])
    row = {"seed": seed, "policy": name, "wall_s": time.time() - t0}
    for p in POLS:
        row[f"gain@{p}"] = (dom.world.expected(fin.files, "evolve", pols[p])["S"]
                            - dom.world.expected(h0.files, "evolve", pols[p])["S"])
    shutil.rmtree(out, ignore_errors=True)
    return row


def ci(x) -> dict:
    x = np.asarray(x, dtype=float)
    _, lo, hi = bootstrap_ci(list(x))
    return {"mean": float(x.mean()), "lo": float(lo), "hi": float(hi), "n": int(len(x))}


def main():
    a = retry_args("E12c: link-curvature vs search split of E12b")
    e12b = json.loads(E12B.read_text())
    strengths = {n: e12b["config"]["strengths"][n] for n in POLS}
    rows = pmap(one, [{"seed": s, "policy": n, "strengths": strengths, "T": a.T} for s in range(a.seeds) for n in POLS],
                a.workers)
    by = {(r["seed"], r["policy"]): r for r in rows}
    seeds = sorted({r["seed"] for r in rows})

    def gain(evolved_by: str, scored_by: str) -> list:
        return [by[(s, evolved_by)][f"gain@{scored_by}"] for s in seeds]

    def diff(x: list, y: list) -> list:
        return [p - q for p, q in zip(x, y)]

    # determinism check against E12b's stored rows (own-policy true evolve gain)
    old = {(r["seed"], r["policy"]): r["evolve_gain"] for r in e12b["rows"] if r["policy"] in POLS}
    max_dev = max(abs(by[(s, n)][f"gain@{n}"] - old[(s, n)]) for s in seeds for n in POLS if (s, n) in old)
    # curvature + search = total, exactly
    parts = {"total (= E12b weak - strong)": ci(diff(gain("weak_p", "weak_p"), gain("strong_p", "strong_p"))),
             "curvature (same STRONG-evolved harness)": ci(diff(gain("strong_p", "weak_p"),
                                                                gain("strong_p", "strong_p"))),
             "search (under WEAK_P, WEAK- minus STRONG-evolved harness)": ci(diff(gain("weak_p", "weak_p"),
                                                                                 gain("strong_p", "weak_p"))),
             "reported only: under STRONG_P, WEAK- minus STRONG-evolved harness":
                 ci(diff(gain("weak_p", "strong_p"), gain("strong_p", "strong_p")))}
    s = parts["search (under WEAK_P, WEAK- minus STRONG-evolved harness)"]
    outcome = ("search-attributable part exists (CI > 0)" if s["lo"] > 0 else
               "search part negative (CI < 0)" if s["hi"] < 0 else "no search-attributable part (CI includes 0)")
    out = {"experiment": "E12c curvature vs search split of E12b (retry round 2 review follow-up: P3b)",
           "config": {"seeds": a.seeds, "T": a.T, "strengths": strengths, "preset": "coding (delta calibrated)"},
           "max_abs_deviation_from_e12b_rows": max_dev, "parts": parts, "outcome": outcome,
           "verdict_L12": "PARTIAL", "rows": rows}
    save("e12c_curvature_split", out)
    print("max dev vs e12b", max_dev)
    for k, v in parts.items():
        print(k, round(v["mean"] * 100, 2), round(v["lo"] * 100, 2), round(v["hi"] * 100, 2))
    print(outcome)


if __name__ == "__main__":
    main()
