"""E16 - The paper's own Lasso artifacts under the paper's own protocol (claims audit Q13 / Q32, retry round 2).

Q13 ("the resulting solvers also outperform the standard sklearn and glmnet implementations on all six held-out
datasets", §4.1) was NOT REPRODUCED in E3, where the mock agent's numpy programs were compared with sklearn on
two tiny synthetic held-out instances. That tested our mock agent, not the claim: the paper's solvers are
C++/Eigen/OpenMP programs (App. C) run on six real datasets. This script tests the claim on the artifact the
paper publishes - the App. C solver ("the Lasso-path solver discovered by Dream-RSI", arXiv source
``appendix/discovered_programs/lasso/code.cpp``, a runnable ``CPP_CODE`` file) - with
:class:`rsi.domains.discovery.SimpleTESLassoDomain` (the benchmark's protocol re-implemented):

* held-out datasets: the ones ``e16_prepare_heldout.py`` can build here (DNA, Leukemia, Colon, a Duke-like
  set; Gisette and RCV1 are unavailable);
* baselines: sklearn's ``lasso_path(n_alphas=50, eps=1e-2)`` (the paper's "sklearn" row and the correctness
  reference; timed in-process, as the benchmark does), R glmnet (``--rscript``; timed inside R on the same
  lambda path, ``standardize=FALSE, intercept=FALSE``, early path termination disabled), and - for context -
  the benchmark's C++ port of glmnet (its seed program) and its best program (the paper's "SimpleTES" row);
* timing: one warm-up, then the minimum of 5 calls (compiled programs: a process per call with binary I/O,
  as in the benchmark), single-threaded (OMP_NUM_THREADS = 1); a secondary pass with 2 threads;
* Q32: the App. C solver on the 17 search shapes (3 evaluations with fresh instances: 51 correctness checks).

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
from rsi.domains.discovery.lasso_cpp import (SimpleTESLassoDomain, load_heldout, max_gap, sklearn_path,  # noqa: E402
                                             timed)

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
    with tempfile.TemporaryDirectory() as d:
        fin, fout, fr = Path(d) / "in.bin", Path(d) / "out.bin", Path(d) / "fit.R"
        fr.write_text(R_SCRIPT)
        Xc = np.ascontiguousarray(X, dtype=np.float64)
        fin.write_bytes(struct.pack("iii", *Xc.shape, len(alphas)) + Xc.tobytes()
                        + np.ascontiguousarray(y, np.float64).tobytes() + np.ascontiguousarray(alphas, np.float64).tobytes())
        rr = subprocess.run([rscript, str(fr), str(fin), str(fout), str(reps), repr(float(thresh))],
                            capture_output=True, text=True,
                            timeout=3600, env={**os.environ, "OMP_NUM_THREADS": "1"})
        if rr.returncode != 0:
            raise RuntimeError(rr.stderr[-800:])
        raw = fout.read_bytes()
        ms = struct.unpack("d", raw[:8])[0]
        k = struct.unpack("i", raw[8:12])[0]
        B = np.frombuffer(raw[12:], dtype=np.float64).reshape((X.shape[1], k), order="F")
        return ms, B


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
                row["glmnet_R"] = {"ms": gms, "gap": max_gap(X, y, B, ref, alphas) if B.shape == ref.shape else math.inf,
                                   "n_lambda": int(B.shape[1])}
            except Exception as e:  # noqa: BLE001 - report, never hide
                row["glmnet_R"] = {"ms": math.inf, "gap": math.inf, "error": str(e)[:400]}
        for label, dom in doms.items():
            binary, err = dom.binary_of(programs[label])
            if err:
                row[label] = {"ms": math.inf, "gap": math.inf, "error": err[:400]}
                continue
            from rsi.domains.discovery.lasso_cpp import run_binary

            try:
                pms, coef = timed(lambda: run_binary(binary, X, y, alphas, threads=dom.threads), reps, warmup=1)
                row[label] = {"ms": pms, "gap": max_gap(X, y, np.asarray(coef), ref, alphas)}
            except Exception as e:  # noqa: BLE001
                row[label] = {"ms": math.inf, "gap": math.inf, "error": str(e)[:400]}
        rows[name] = row
        print(f"[held-out {name} {X.shape}] " + "  ".join(f"{k}={v['ms']:.1f}ms(gap {v['gap']:.1e})"
                                                        for k, v in row.items() if isinstance(v, dict)), flush=True)
    return rows, missing


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
    programs = {}
    for spec in a.program:
        label, _, path = spec.partition("=")
        programs[label] = Artifact({"solver.py": Path(path).read_text()})
    t0 = time.time()
    out = {"config": {"programs": {s.partition("=")[0]: s.partition("=")[2] for s in a.program}, "reps": a.reps,
                      "protocol": "SimpleTES generate_results.py: 1 warm-up + min of reps; lasso_path(n_alphas=50, "
                                  "eps=1e-2) alphas and reference; gap <= 1e-6", "search_evals": a.search_evals,
                      "rscript": a.rscript, "heldout_manifest": json.loads((Path(a.heldout) / "manifest.json").read_text())
                      if (Path(a.heldout) / "manifest.json").exists() else None}}
    passes = {}
    for threads in (1, 2):
        doms = {k: SimpleTESLassoDomain(eigen_dir=a.eigen, heldout_dir=a.heldout, threads=threads, heldout_reps=a.reps,
                                        cache_dir=a.cache) for k in programs}
        rows, missing = heldout_table(programs, doms, a.heldout, a.rscript if threads == 1 else None, a.reps)
        out[f"heldout_threads{threads}"] = rows
        out["missing"] = missing
    # ---- Q13: App. C vs sklearn and R glmnet on every available dataset (1 thread: the benchmark's setting)
    h = out["heldout_threads1"]
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
    passes["Q13"] = {"per_dataset": q13, "datasets": list(h), "missing": out["missing"],
                     "all_available_pass": bool(q13) and all(v["correct"] and v["faster_than_sklearn"] and
                                                             v["faster_than_glmnet_R"] is not False for v in q13.values()),
                     "glmnet_measured": all(v["faster_than_glmnet_R"] is not None for v in q13.values())}
    # ---- Q32: correctness on 17 shapes x search_evals fresh instances + speed vs sklearn and the glmnet port
    sd = SimpleTESLassoDomain(eigen_dir=a.eigen, threads=1, timing_runs=3, cache_dir=a.cache)
    search = {}
    labels = [k for k in ("dream_appC", "glmnet_port", "simpletes_best") if k in programs]
    for k in labels + ["sklearn"]:
        evs = []
        for e in range(a.search_evals):
            if k == "sklearn":
                ev = sd.evaluate_solver(lambda X, y, lam: __import__("sklearn.linear_model", fromlist=["x"])
                                        .lasso_path(X, y, alphas=lam)[1], "search", eval_seed=e)
            else:
                ev = sd.evaluate_split(programs[k], "search", eval_seed=e)
            evs.append({"fail_class": ev.fail_class, "score": ev.score, "error": ev.error,
                        "geomean_ms": ev.diagnostics.get("geomean_ms"), "max_gap": ev.diagnostics.get("max_gap"),
                        "problems": ev.diagnostics.get("problems")})
            print(f"[search {k} eval {e}] {ev.fail_class} score {ev.score:.4f} geomean "
                  f"{ev.diagnostics.get('geomean_ms')} max_gap {ev.diagnostics.get('max_gap')}", flush=True)
        search[k] = evs
    out["search"] = search

    def gm(k):
        return float(np.exp(np.mean([math.log(e["geomean_ms"]) for e in search[k] if e["geomean_ms"]])))

    appc_ok = all(e["fail_class"] == "ok" for e in search.get("dream_appC", []))
    q32 = {"correct_all_search_problems": appc_ok,
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
    # ---- context (Q14 / Q33): App. C vs the benchmark's best program per dataset
    if "simpletes_best" in programs:
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
