"""E16c: symmetric compute-only timing of the App. C Lasso solver vs sklearn and R glmnet (Q13).

    python experiments/dream-rsi/e16c_symmetric_timing.py --progs <dir with dream_appC.py ...> --eigen <dir> \
        --heldout <dir> --rscript <Rscript> --cache <cpp cache dir>
"""
import argparse
import json
import math
import os
import statistics
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")

import numpy as np  # noqa: E402

from rsi.core.artifact import Artifact  # noqa: E402
from rsi.domains.discovery.lasso_cpp import (SimpleTESLassoDomain, load_heldout, max_gap, run_binary,  # noqa: E402
                                             sklearn_path)

RESULTS = ROOT / "results" / "dream-rsi"
THRESH = (1e-7, 1e-9, 1e-11, 1e-13)
BLOCKS, REPS = 3, 5
PROGRAMS = {"dream_appC": "dream_appC.py", "glmnet_port": "simpletes_init_glmnet_port.py",
            "simpletes_best": "simpletes_best.py"}

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
reps <- as.integer(args[3]); thresh <- as.numeric(args[4]); warm <- as.integer(args[5])
glmnet.control(fdev = 0, devmax = 1.0)
fit_once <- function() glmnet(X, y, family = "gaussian", lambda = lam, standardize = FALSE, intercept = FALSE,
                              thresh = thresh)
for (i in seq_len(warm)) fit <- fit_once()
ts <- numeric(reps)
for (i in seq_len(reps)) {
  t0 <- as.numeric(Sys.time())
  fit <- fit_once()
  ts[i] <- as.numeric(Sys.time()) - t0
}
B <- as.matrix(coef(fit))[-1, , drop = FALSE]
con <- file(args[2], "wb")
writeBin(as.integer(reps), con, size = 4)
writeBin(as.double(ts * 1000), con)
writeBin(as.integer(ncol(B)), con, size = 4)
writeBin(as.double(B), con)
close(con)
'''


def build(src: Path, out: Path, shared=False):
    subprocess.run(["gcc", "-O2", str(src), "-o", str(out)] + (["-shared", "-fPIC", "-ldl"] if shared else []),
                   check=True)
    return str(out)


class Timer:
    """Runs a binary under the ``e16c/solve_timer.c`` LD_PRELOAD shim (last stdin fread -> first/last stdout fwrite)."""

    def __init__(self, shim: str, tmp: Path):
        self.shim, self.out = shim, tmp / "solve_timer.txt"

    def call(self, binary, X, y, alphas):
        """One call through the E16 harness: (harness ms, first-write ms, last-write ms, coef)."""
        self.out.unlink(missing_ok=True)
        old = {k: os.environ.get(k) for k in ("LD_PRELOAD", "RSI_SOLVE_TIMER_OUT")}
        os.environ["LD_PRELOAD"], os.environ["RSI_SOLVE_TIMER_OUT"] = self.shim, str(self.out)
        try:
            t0 = time.perf_counter()
            coef = run_binary(binary, X, y, alphas, threads=1)
            harness = (time.perf_counter() - t0) * 1e3
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        first, last = (int(v) / 1e6 for v in self.out.read_text().split())
        return harness, first, last, coef


def glmnet_block(rscript, fin: Path, tmp: Path, thresh, p, warm=1):
    """REPS glmnet() fits in one R process, each timed with Sys.time() around the call alone."""
    fout, fr = tmp / "r_out.bin", tmp / "fit.R"
    fr.write_text(R_SCRIPT)
    rr = subprocess.run([rscript, str(fr), str(fin), str(fout), str(REPS), repr(float(thresh)), str(warm)],
                        capture_output=True, text=True, timeout=3600, env={**os.environ, "OMP_NUM_THREADS": "1"})
    if rr.returncode != 0:
        raise RuntimeError(rr.stderr[-800:])
    raw = fout.read_bytes()
    reps = struct.unpack("i", raw[:4])[0]
    ts = list(struct.unpack(f"{reps}d", raw[4:4 + 8 * reps]))
    k = struct.unpack("i", raw[4 + 8 * reps:8 + 8 * reps])[0]
    return ts, np.frombuffer(raw[8 + 8 * reps:], dtype=np.float64).reshape((p, k), order="F")


def summ(xs):
    return {"min": min(xs), "median": statistics.median(xs), "n": len(xs), "all": xs}


def measure(X, y, alphas, ref, bins, noop, timer, rscript, tmp, fin):
    """1 warm-up per method, then BLOCKS x REPS timings with the blocks interleaved (the order rotates per block)."""
    rec = {k: {"harness": [], "first": [], "last": []} for k in list(bins) + ["noop"]}
    rec["sklearn"] = {"compute": []}
    for th in THRESH:
        rec[f"glmnet_R_{th:g}"] = {"compute": []}
    gaps = {}
    for label, b in {**bins, "noop": noop}.items():  # the R warm-up runs inside every R process
        _, _, _, coef = timer.call(b, X, y, alphas)
        if label != "noop":
            gaps[label] = max_gap(X, y, np.asarray(coef), ref, alphas)
    sklearn_path(X, y)
    gaps["sklearn"] = 0.0
    methods = list(bins) + ["noop", "sklearn"] + [f"glmnet_R_{th:g}" for th in THRESH]
    for blk in range(BLOCKS):
        for m in methods[blk % len(methods):] + methods[:blk % len(methods)]:
            if m in bins or m == "noop":
                for _ in range(REPS):
                    h, f1, f2, _ = timer.call(bins.get(m, noop), X, y, alphas)
                    rec[m]["harness"].append(h)
                    rec[m]["first"].append(f1)
                    rec[m]["last"].append(f2)
            elif m == "sklearn":
                for _ in range(REPS):
                    t0 = time.perf_counter()
                    sklearn_path(X, y)
                    rec[m]["compute"].append((time.perf_counter() - t0) * 1e3)
            else:
                ts, B = glmnet_block(rscript, fin, tmp, float(m.rsplit("_", 1)[1]), X.shape[1])
                rec[m]["compute"].extend(ts)
                gaps[m] = max_gap(X, y, B, ref, alphas) if B.shape == ref.shape else math.inf
    return rec, gaps


def row_of(name, X, rec, gaps, bins) -> dict:
    """Per-dataset summary; Q13 passes iff App. C is correct and faster than sklearn and every gate-passing glmnet."""
    noop_min = min(rec["noop"]["harness"])
    row = {"shape": list(X.shape), "gaps": gaps, "noop_harness": summ(rec["noop"]["harness"])}
    for label in bins:
        r = rec[label]
        row[label] = {"compute_first_write": summ(r["first"]), "compute_last_write": summ(r["last"]),
                      "harness": summ(r["harness"]), "harness_minus_noop_min": min(r["harness"]) - noop_min}
    row["sklearn"] = {"compute": summ(rec["sklearn"]["compute"])}
    for th in THRESH:
        m = f"glmnet_R_{th:g}"
        row[m] = {"compute": summ(rec[m]["compute"]), "gap": gaps[m], "passes_gate": bool(gaps[m] <= 1e-6)}
    passing = [f"glmnet_R_{th:g}" for th in THRESH if row[f"glmnet_R_{th:g}"]["passes_gate"]]
    best_pass = min(passing, key=lambda m: row[m]["compute"]["min"]) if passing else None
    appc = row["dream_appC"]
    prim = appc["compute_first_write"]["min"]
    sk = row["sklearn"]["compute"]
    row["q13"] = {
        "appC_gap_ok": bool(gaps["dream_appC"] <= 1e-6),
        "appC_primary_ms": prim,
        "sklearn_ms": sk["min"],
        "fastest_gate_passing_glmnet": best_pass,
        "fastest_gate_passing_glmnet_ms": row[best_pass]["compute"]["min"] if best_pass else None,
        "glmnet_default_ms": row["glmnet_R_1e-07"]["compute"]["min"],
        "faster_than_sklearn": prim < sk["min"],
        "faster_than_gate_passing_glmnet": (prim < row[best_pass]["compute"]["min"]) if best_pass else True,
        "faster_than_default_glmnet": prim < row["glmnet_R_1e-07"]["compute"]["min"],
        "secondary_harness_minus_noop_ms": appc["harness_minus_noop_min"],
        "secondary_faster_than_gate_passing_glmnet": (appc["harness_minus_noop_min"]
                                                      < row[best_pass]["compute"]["min"]) if best_pass else True,
        "secondary_faster_than_sklearn": appc["harness_minus_noop_min"] < sk["min"],
        "median_faster_than_gate_passing_glmnet": (appc["compute_first_write"]["median"]
                                                   < row[best_pass]["compute"]["median"]) if best_pass else True,
        "median_faster_than_sklearn": appc["compute_first_write"]["median"] < sk["median"],
    }
    q = row["q13"]
    q["passes"] = bool(q["appC_gap_ok"] and q["faster_than_sklearn"] and q["faster_than_gate_passing_glmnet"])
    print(f"[{name} {X.shape}] appC {prim:.2f} ms (harness-noop {appc['harness_minus_noop_min']:.2f}, "
          f"harness {appc['harness']['min']:.2f}, noop {noop_min:.2f}) | sklearn {q['sklearn_ms']:.2f} | "
          f"glmnet default {q['glmnet_default_ms']:.2f} (gap {gaps['glmnet_R_1e-07']:.1e}) | "
          f"gate-passing {best_pass} {q['fastest_gate_passing_glmnet_ms']} | port "
          f"{row['glmnet_port']['compute_first_write']['min']:.2f} | simpletes_best (last-write) "
          f"{row['simpletes_best']['compute_last_write']['min']:.2f} | passes {q['passes']}", flush=True)
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--progs", required=True)
    ap.add_argument("--eigen", required=True)
    ap.add_argument("--heldout", required=True)
    ap.add_argument("--rscript", required=True)
    ap.add_argument("--cache", default=None)
    ap.add_argument("--out", default=str(RESULTS / "e16c_symmetric_timing.json"))
    a = ap.parse_args()
    t_start = time.time()
    load_start = os.getloadavg()
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        shim = build(HERE / "e16c" / "solve_timer.c", tmp / "solve_timer.so", shared=True)
        noop = build(HERE / "e16c" / "noop.c", tmp / "noop")
        dom = SimpleTESLassoDomain(eigen_dir=a.eigen, threads=1, cache_dir=a.cache)
        bins = {}
        for label, f in PROGRAMS.items():
            b, err = dom.binary_of(Artifact({"solver.py": (Path(a.progs) / f).read_text()}))
            if err:
                raise RuntimeError(f"{label}: {err}")
            bins[label] = b
        timer = Timer(shim, tmp)
        data, missing = load_heldout(a.heldout)
        rows = {}
        for name, (X, y) in data.items():
            X = np.ascontiguousarray(X, dtype=np.float64)
            y = np.ascontiguousarray(y, dtype=np.float64)
            alphas, ref = sklearn_path(X, y)
            fin = tmp / "in.bin"
            fin.write_bytes(struct.pack("iii", *X.shape, len(alphas)) + X.tobytes() + y.tobytes()
                            + np.ascontiguousarray(alphas, np.float64).tobytes())
            rec, gaps = measure(X, y, alphas, ref, bins, noop, timer, a.rscript, tmp, fin)
            rows[name] = row_of(name, X, rec, gaps, bins)
    res = {"experiment": "e16c_symmetric_timing", "created": time.strftime("%Y-%m-%d %H:%M:%S"),
           "config": {"blocks": BLOCKS, "reps": REPS, "thresh": THRESH, "threads": 1, "programs": PROGRAMS,
                      "timer": "LD_PRELOAD e16c/solve_timer.c (CLOCK_MONOTONIC, last stdin fread -> first/last "
                               "stdout fwrite); R: Sys.time around glmnet(); sklearn: perf_counter in-process",
                      "loadavg_start": load_start, "loadavg_end": os.getloadavg()},
           "missing": missing, "rows": rows,
           "q13_passes_all_available": bool(rows) and all(r["q13"]["passes"] for r in rows.values()),
           "q13_datasets_passing": [n for n, r in rows.items() if r["q13"]["passes"]],
           "wall_s": round(time.time() - t_start, 1)}
    Path(a.out).write_text(json.dumps(res, indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o)))
    print(json.dumps({n: r["q13"] for n, r in rows.items()}, indent=1, default=str))
    print(f"[Q13 passes on all available datasets] {res['q13_passes_all_available']}  [saved] {a.out}")


if __name__ == "__main__":
    main()
