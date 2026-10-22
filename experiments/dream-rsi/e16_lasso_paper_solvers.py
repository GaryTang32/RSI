"""E16: the paper's App. C Lasso solver vs sklearn and R glmnet on held-out datasets (Q13) and the search shapes (Q32).

    python experiments/dream-rsi/e16_lasso_paper_solvers.py --program dream_appC=<code.cpp> \
        [--program simpletes_best=<file> --program glmnet_port=<file>] --eigen <eigen dir> --heldout <dir> \
        [--rscript <Rscript>] [--search-evals 3]
"""
import argparse
import json
import math
import os
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")

import numpy as np  # noqa: E402

from rsi.core.artifact import Artifact  # noqa: E402
from rsi.domains.discovery.lasso_cpp import (SimpleTESLassoDomain, load_heldout, max_gap, run_binary,  # noqa: E402
                                             sklearn_path, timed)

RESULTS = ROOT / "results" / "dream-rsi"

R_SCRIPT = r'''
suppressMessages(library(glmnet))
args <- commandArgs(trailingOnly = TRUE)
con <- file(args[1], "rb")
hdr <- readBin(con, "integer", 3, size = 4)
n <- hdr[1]; p <- hdr[2]; K <- hdr[3]
X <- matrix(readBin(con, "double", n * p), nrow = n, ncol = p, byrow = TRUE)
y <- readBin(con, "double", n)
lam <- readBin(con, "double", K)
close(con)
reps <- as.integer(args[3])
thresh <- if (length(args) >= 4) as.numeric(args[4]) else 1e-7   # glmnet's default convergence threshold
glmnet.control(fdev = 0, devmax = 1.0)      # never stop the path early: all K lambdas are required
fit_once <- function() glmnet(X, y, family = "gaussian", lambda = lam, standardize = FALSE, intercept = FALSE,
                              thresh = thresh)
fit <- fit_once()                            # warm-up
best <- Inf
for (i in seq_len(reps)) {
  t0 <- proc.time()[["elapsed"]]
  fit <- fit_once()
  best <- min(best, proc.time()[["elapsed"]] - t0)
}
B <- as.matrix(coef(fit))[-1, , drop = FALSE]   # drop the intercept row: p x K
con <- file(args[2], "wb")
writeBin(as.double(best * 1000), con)
writeBin(as.integer(ncol(B)), con, size = 4)
writeBin(as.double(B), con)                  # column-major
close(con)
'''


def glmnet_r(rscript, X, y, alphas, reps=5, thresh=1e-7):
    """R glmnet on the same lambda path, timed inside R: (best ms, p x K coefficients)."""
    with tempfile.TemporaryDirectory() as d:
        fin, fout, fr = Path(d) / "in.bin", Path(d) / "out.bin", Path(d) / "fit.R"
        fr.write_text(R_SCRIPT)
        Xc = np.ascontiguousarray(X, dtype=np.float64)
        fin.write_bytes(struct.pack("iii", *Xc.shape, len(alphas)) + Xc.tobytes()
                        + np.ascontiguousarray(y, np.float64).tobytes()
                        + np.ascontiguousarray(alphas, np.float64).tobytes())
        rr = subprocess.run([rscript, str(fr), str(fin), str(fout), str(reps), repr(float(thresh))],
                            capture_output=True, text=True, timeout=3600, env={**os.environ, "OMP_NUM_THREADS": "1"})
        if rr.returncode != 0:
            raise RuntimeError(rr.stderr[-800:])
        raw = fout.read_bytes()
        ms = struct.unpack("d", raw[:8])[0]
        k = struct.unpack("i", raw[8:12])[0]
        return ms, np.frombuffer(raw[12:], dtype=np.float64).reshape((X.shape[1], k), order="F")


def _failed(e) -> dict:
    return {"ms": math.inf, "gap": math.inf, "error": str(e)[:400]}


def heldout_table(programs, doms, heldout_dir, rscript, reps):
    data, missing = load_heldout(heldout_dir)
    rows = {}
    for name, (X, y) in data.items():
        X = np.ascontiguousarray(X, dtype=np.float64)
        y = np.ascontiguousarray(y, dtype=np.float64)
        alphas, ref = sklearn_path(X, y)
        row = {"shape": list(X.shape)}
        ms, _ = timed(lambda: sklearn_path(X, y), reps, warmup=1)
        row["sklearn"] = {"ms": ms, "gap": 0.0}
        if rscript:
            try:
                gms, B = glmnet_r(rscript, X, y, alphas, reps)
                gap = max_gap(X, y, B, ref, alphas) if B.shape == ref.shape else math.inf
                row["glmnet_R"] = {"ms": gms, "gap": gap, "n_lambda": int(B.shape[1])}
            except Exception as e:  # noqa: BLE001 - report, never hide
                row["glmnet_R"] = _failed(e)
        for label, dom in doms.items():
            binary, err = dom.binary_of(programs[label])
            if err:
                row[label] = _failed(err)
                continue
            try:
                pms, coef = timed(lambda: run_binary(binary, X, y, alphas, threads=dom.threads), reps, warmup=1)
                row[label] = {"ms": pms, "gap": max_gap(X, y, np.asarray(coef), ref, alphas)}
            except Exception as e:  # noqa: BLE001
                row[label] = _failed(e)
        rows[name] = row
        print(f"[held-out {name} {X.shape}] " + "  ".join(f"{k}={v['ms']:.1f}ms(gap {v['gap']:.1e})"
                                                        for k, v in row.items() if isinstance(v, dict)), flush=True)
    return rows, missing


def q13_of(h: dict) -> dict:
    """App. C vs sklearn and R glmnet on every available dataset (1 thread: the benchmark's setting)."""
    q13 = {}
    for name, row in h.items():
        d = row.get("dream_appC", {})
        q13[name] = {"correct": bool(d.get("gap", math.inf) <= 1e-6),
                     "faster_than_sklearn": bool(d.get("ms", math.inf) < row["sklearn"]["ms"]),
                     "faster_than_glmnet_R": bool(d.get("ms", math.inf) < row["glmnet_R"]["ms"]) if "glmnet_R" in row
                     else None,
                     "speedup_vs_sklearn": row["sklearn"]["ms"] / d["ms"] if d.get("ms") else None,
                     "speedup_vs_glmnet_R": (row["glmnet_R"]["ms"] / d["ms"]) if "glmnet_R" in row and d.get("ms")
                     else None}
    return q13


def search_evals(programs, eigen, cache, n_evals) -> dict:
    """Every solver on the 17 search shapes, ``n_evals`` evaluations with fresh instances each."""
    from sklearn.linear_model import lasso_path

    sd = SimpleTESLassoDomain(eigen_dir=eigen, threads=1, timing_runs=3, cache_dir=cache)
    search = {}
    for k in [k for k in ("dream_appC", "glmnet_port", "simpletes_best") if k in programs] + ["sklearn"]:
        evs = []
        for e in range(n_evals):
            if k == "sklearn":
                ev = sd.evaluate_solver(lambda X, y, lam: lasso_path(X, y, alphas=lam)[1], "search", eval_seed=e)
            else:
                ev = sd.evaluate_split(programs[k], "search", eval_seed=e)
            evs.append({"fail_class": ev.fail_class, "score": ev.score, "error": ev.error,
                        "geomean_ms": ev.diagnostics.get("geomean_ms"), "max_gap": ev.diagnostics.get("max_gap"),
                        "problems": ev.diagnostics.get("problems")})
            print(f"[search {k} eval {e}] {ev.fail_class} score {ev.score:.4f} geomean "
                  f"{ev.diagnostics.get('geomean_ms')} max_gap {ev.diagnostics.get('max_gap')}", flush=True)
        search[k] = evs
    return search


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--program", action="append", required=True, help="label=path (a CPP_CODE file)")
    ap.add_argument("--eigen", required=True)
    ap.add_argument("--heldout", required=True)
    ap.add_argument("--rscript", default=None)
    ap.add_argument("--reps", type=int, default=5)
    ap.add_argument("--search-evals", type=int, default=3)
    ap.add_argument("--cache", default=None)
    ap.add_argument("--out", default=str(RESULTS / "e16_lasso_paper_solvers.json"))
    a = ap.parse_args()
    specs = [s.partition("=") for s in a.program]
    programs = {label: Artifact({"solver.py": Path(path).read_text()}) for label, _, path in specs}
    t0 = time.time()
    manifest = Path(a.heldout) / "manifest.json"
    out = {"config": {"programs": {label: path for label, _, path in specs}, "reps": a.reps,
                      "protocol": "SimpleTES generate_results.py: 1 warm-up + min of reps; lasso_path(n_alphas=50, "
                                  "eps=1e-2) alphas and reference; gap <= 1e-6", "search_evals": a.search_evals,
                      "rscript": a.rscript, "heldout_manifest": json.loads(manifest.read_text())
                      if manifest.exists() else None}}
    for threads in (1, 2):  # the benchmark's single thread; a secondary pass with 2
        doms = {k: SimpleTESLassoDomain(eigen_dir=a.eigen, heldout_dir=a.heldout, threads=threads, heldout_reps=a.reps,
                                        cache_dir=a.cache) for k in programs}
        rows, missing = heldout_table(programs, doms, a.heldout, a.rscript if threads == 1 else None, a.reps)
        out[f"heldout_threads{threads}"] = rows
        out["missing"] = missing
    h = out["heldout_threads1"]
    q13 = q13_of(h)
    passes = {"Q13": {"per_dataset": q13, "datasets": list(h), "missing": out["missing"],
                      "all_available_pass": bool(q13) and all(v["correct"] and v["faster_than_sklearn"] and
                                                              v["faster_than_glmnet_R"] is not False
                                                              for v in q13.values()),
                      "glmnet_measured": all(v["faster_than_glmnet_R"] is not None for v in q13.values())}}
    search = out["search"] = search_evals(programs, a.eigen, a.cache, a.search_evals)

    def gm(k):
        return float(np.exp(np.mean([math.log(e["geomean_ms"]) for e in search[k] if e["geomean_ms"]])))

    # Q32: correctness on 17 shapes x search_evals fresh instances + speed vs sklearn and the glmnet port
    q32 = {"correct_all_search_problems": all(e["fail_class"] == "ok" for e in search.get("dream_appC", [])),
           "n_correctness_checks": 17 * a.search_evals,
           "correct_all_heldout": all(v["correct"] for v in q13.values()),
           "geomean_ms": {k: gm(k) for k in search},
           "faster_than_sklearn_search": gm("dream_appC") < gm("sklearn") if "dream_appC" in search else None,
           "faster_than_glmnet_port_search": gm("dream_appC") < gm("glmnet_port") if "glmnet_port" in search else None,
           "faster_than_sklearn_every_heldout": all(v["faster_than_sklearn"] for v in q13.values())}
    q32["passes"] = bool(q32["correct_all_search_problems"] and q32["correct_all_heldout"]
                         and q32["faster_than_sklearn_search"] and q32["faster_than_glmnet_port_search"]
                         and q32["faster_than_sklearn_every_heldout"])
    passes["Q32"] = q32
    if "simpletes_best" in programs:  # context (Q14 / Q33): App. C vs the benchmark's best program per dataset
        passes["Q14_context_appC_vs_simpletes_best"] = {
            n: {"appC_ms": r["dream_appC"]["ms"], "simpletes_best_ms": r["simpletes_best"]["ms"],
                "appC_faster": r["dream_appC"]["ms"] < r["simpletes_best"]["ms"]} for n, r in h.items()}
    out["tests"] = passes
    out["wall_s"] = round(time.time() - t0, 1)
    out["created"] = time.strftime("%Y-%m-%d %H:%M:%S")
    Path(a.out).write_text(json.dumps(out, indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o)))
    print(json.dumps(passes, indent=1, default=str))
    print(f"[saved] {a.out}")


if __name__ == "__main__":
    main()
