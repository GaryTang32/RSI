"""E12c (retry round 2 review follow-up, preregistered P3b in docs/methods/rrsi/claims-audit.md section 6): claim L12.

E12b found that, at the paper's starting levels, the weaker search policy gains +5.4 pts more on the
evolve set. With neutral component scales the two policies differ only by an additive logit offset,
so part of that difference is the logistic link (the same harness moves the score more near 0.65
than near 0.74). This re-runs E12b's STRONG_P and WEAK_P arms exactly and scores every final
harness under BOTH policies, splitting the contrast (paired by world) into

  curvature = gain(STRONG-evolved harness | WEAK_P) - gain(STRONG-evolved harness | STRONG_P)
  search    = gain(WEAK-evolved harness | WEAK_P)   - gain(STRONG-evolved harness | WEAK_P)

which sum exactly to E12b's weak - strong contrast. Test: search-part CI lower bound > 0.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np  # noqa: E402
from _common import pmap, save, search_llm, switches  # noqa: E402
from e12b_policy_headroom import SCALES  # noqa: E402

from rsi.core.ledger import ArtifactStore  # noqa: E402
from rsi.core.stats import bootstrap_ci  # noqa: E402
from rsi.domains.harnessworld import make_domain  # noqa: E402
from rsi.domains.harnessworld.world import Policy  # noqa: E402
from rsi.rrsi import Config  # noqa: E402
from rsi.rrsi.config import PRESETS  # noqa: E402

E12B = Path(__file__).resolve().parents[2] / "results" / "rrsi" / "e12b_policy_headroom.json"
POLS = ("strong_p", "weak_p")


def one(job: dict) -> dict:
    from rsi.rrsi import run
    seed, name, strengths = job["seed"], job["policy"], job["strengths"]
    pols = {n: Policy(n, strengths[n], SCALES[n]) for n in POLS}
    dom = make_domain(seed=seed, policy=pols[name])
    cfg = dict(PRESETS["coding"])
    cfg.update(delta=None, workers=1, seed=seed, T=job["T"])
    out = Path(tempfile.mkdtemp(prefix=f"e12c_{name}_{seed}_", dir=job["scratch"]))
    t0 = time.time()
    res = run(dom, dom.seed_artifact(), llm_propose=search_llm("sim", dom.world), config=Config(**cfg),
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
    ap = argparse.ArgumentParser(description="E12c: link-curvature vs search split of E12b")
    ap.add_argument("--seeds", type=int, default=100)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--T", type=int, default=20)
    a = ap.parse_args()
    e12b = json.loads(E12B.read_text())
    strengths = {n: e12b["config"]["strengths"][n] for n in POLS}
    scratch = Path(os.environ.get("RRSI_SCRATCH", tempfile.gettempdir())) / "rrsi_runs"
    scratch.mkdir(parents=True, exist_ok=True)
    jobs = [{"seed": s, "policy": n, "strengths": strengths, "T": a.T, "scratch": str(scratch)}
            for s in range(a.seeds) for n in POLS]
    rows = pmap(one, jobs, a.workers)
    by = {(r["seed"], r["policy"]): r for r in rows}
    seeds = sorted({r["seed"] for r in rows})
    # determinism check against E12b's stored rows (own-policy true evolve gain)
    old = {(r["seed"], r["policy"]): r["evolve_gain"] for r in e12b["rows"] if r["policy"] in POLS}
    max_dev = max(abs(by[(s, n)][f"gain@{n}"] - old[(s, n)]) for s in seeds for n in POLS if (s, n) in old)
    total = [by[(s, "weak_p")]["gain@weak_p"] - by[(s, "strong_p")]["gain@strong_p"] for s in seeds]
    curv = [by[(s, "strong_p")]["gain@weak_p"] - by[(s, "strong_p")]["gain@strong_p"] for s in seeds]
    search = [by[(s, "weak_p")]["gain@weak_p"] - by[(s, "strong_p")]["gain@weak_p"] for s in seeds]
    search_at_strong = [by[(s, "weak_p")]["gain@strong_p"] - by[(s, "strong_p")]["gain@strong_p"] for s in seeds]
    parts = {"total (= E12b weak - strong)": ci(total), "curvature (same STRONG-evolved harness)": ci(curv),
             "search (under WEAK_P, WEAK- minus STRONG-evolved harness)": ci(search),
             "reported only: under STRONG_P, WEAK- minus STRONG-evolved harness": ci(search_at_strong)}
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
