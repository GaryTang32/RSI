"""E16b - R glmnet at convergence thresholds that pass the benchmark's 1e-6 gate (claims audit Q13, retry round 2;
informative follow-up, preregistered as Addendum 2 of claims-audit §6).

In E16 pass 1, R glmnet at its default ``thresh = 1e-7`` was faster than the App. C solver on 3 of 4 held-out
datasets but failed the 1e-6 objective gate on all four. This re-times glmnet at thresh in {1e-7, 1e-9, 1e-11,
1e-13} (same lambda path, ``standardize=FALSE, intercept=FALSE``, one warm-up + min of 5) and compares the App. C
solver's pass-1 time with the fastest glmnet setting that passes the gate on each dataset. It does not change
the Q13 verdict.

    python experiments/dream-rsi/e16b_glmnet_tolerance.py --heldout <dir> --rscript <Rscript>
"""
import argparse
import json
import math
import time
from pathlib import Path

import numpy as np

from e16_lasso_paper_solvers import RESULTS, glmnet_r
from rsi.domains.discovery.lasso_cpp import load_heldout, max_gap, sklearn_path

THRESH = (1e-7, 1e-9, 1e-11, 1e-13)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--heldout", required=True)
    ap.add_argument("--rscript", required=True)
    ap.add_argument("--e16", default=str(RESULTS / "e16_lasso_paper_solvers.json"))
    a = ap.parse_args()
    e16 = json.loads(Path(a.e16).read_text())["heldout_threads1"]
    data, _ = load_heldout(a.heldout)
    out = {}
    for name, (X, y) in data.items():
        alphas, ref = sklearn_path(X, y)
        rows = []
        for th in THRESH:
            ms, B = glmnet_r(a.rscript, X, y, alphas, 5, th)
            gap = max_gap(X, y, B, ref, alphas) if B.shape == ref.shape else math.inf
            rows.append({"thresh": th, "ms": ms, "gap": gap, "passes_gate": bool(gap <= 1e-6)})
            print(f"[{name}] thresh {th:g}: {ms:.1f} ms, gap {gap:.2e}", flush=True)
        ok = [r for r in rows if r["passes_gate"]]
        best = min(ok, key=lambda r: r["ms"]) if ok else None
        appc = e16[name]["dream_appC"]["ms"]
        out[name] = {"settings": rows, "fastest_passing": best, "appC_ms_pass1": appc,
                     "appC_faster_than_fastest_passing_glmnet": (appc < best["ms"]) if best else None}
    res = {"experiment": "e16b_glmnet_tolerance", "created": time.strftime("%Y-%m-%d %H:%M:%S"),
           "results": out, "appC_faster_on": [n for n, r in out.items() if r["appC_faster_than_fastest_passing_glmnet"]]}
    (RESULTS / "e16b_glmnet_tolerance.json").write_text(json.dumps(res, indent=1, default=float))
    print(json.dumps({n: (r["fastest_passing"], r["appC_ms_pass1"]) for n, r in out.items()}, default=str))


if __name__ == "__main__":
    main()
