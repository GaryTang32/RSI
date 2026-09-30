"""Lasso regularization path under the paper's actual protocol: compiled C++ solvers [paper:§4.1, App. A
Problem 1, App. C].

The paper "follow[s] the benchmark setting of SimpleTES": the candidate is a Python file that defines
``CPP_CODE`` (C++ source) and optionally ``COMPILE_FLAGS``; the evaluator compiles it with g++ (Eigen
available) and runs the binary once per problem, passing the problem on stdin and reading the coefficient
path from stdout. App. C's discovered solver is such a file. :class:`LassoPathDomain` (``lasso.py``) is the
earlier numpy stand-in for the offline mock agent; this domain is the faithful setting for any agent that can
write C++ (claims audit Q13/Q31/Q32, retry round 2).

Protocol (re-implemented here from the benchmark's specification, not copied):

* **Wire format.** stdin: ``int32 n, int32 p, int32 K``, then ``X`` (``n*p`` float64, row-major), ``y`` (``n``),
  ``lambdas`` (``K``, decreasing). stdout: ``p*K`` float64, column-major (column k = coefficients at
  ``lambdas[k]``).
* **Search split** (``evaluate_split(art, "search")``): the 17 problem shapes of :data:`SIZES` (five
  generators: ``gaussian``, ``sparse``, ``dense_sol``, ``corr_low``, ``corr_high``). For every shape,
  ``timing_runs`` fresh instances are timed (minimum wall time of the binary call, process start and I/O
  included) and one further fresh instance checks correctness: ``F_k(w_k) <= F_k(w_k^sklearn) + 1e-6`` for
  every k, where ``w^sklearn`` is ``sklearn.linear_model.lasso_path(X, y, n_alphas=50, eps=1e-2)`` and its
  alphas are the lambda path. Any failure scores 0; otherwise the score is ``1 / geomean(ms)``.
* **Held-out split** (``"holdout"``): the paper's six real datasets (Gisette, RCV1, DNA, Leukemia, Colon,
  Duke Breast), read as ``<name>.npz`` (arrays ``X``, ``y``) from ``heldout_dir``; datasets that are not
  there are skipped and listed in the diagnostics. Timing: one warm-up, then the minimum of ``heldout_reps``
  calls; correctness as above against sklearn's path on the same data.
* All solvers run single-threaded by default (``threads=1``; the benchmark pins OpenMP/BLAS threads to 1).

The seed program (:data:`SEED_CPP`) is a plain cyclic coordinate-descent path solver with warm starts, written
for this module (no Eigen needed). There is no offline mock agent: a C++-writing agent is required.
"""
from __future__ import annotations

import ast
import hashlib
import math
import os
import struct
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional, Sequence

import numpy as np

from ...core.artifact import Artifact
from ...core.tasks import Task, TaskSuite
from ...dream.agent import EvalOutcome
from .base import ProgramDomain

PROGRAM = "solver.py"
N_LAMBDA = 50
EPS = 1e-2
TOL_OBJECTIVE = 1e-6
#: the 17 search problems (n, p, generator) of the benchmark the paper follows [paper:§4.1 "the same 17
#: synthetic instances as SimpleTES"]
SIZES: tuple = (
    (200, 100, "gaussian"), (200, 500, "gaussian"), (500, 1000, "gaussian"), (200, 2000, "gaussian"),
    (50, 3000, "gaussian"), (2000, 500, "gaussian"), (100, 5000, "gaussian"), (1000, 500, "sparse"),
    (500, 1000, "sparse"), (500, 1000, "dense_sol"), (500, 30, "gaussian"), (1000, 50, "gaussian"),
    (2000, 3000, "sparse"), (500, 200, "corr_low"), (500, 500, "corr_high"), (200, 1000, "corr_low"),
    (1000, 1000, "corr_high"),
)
#: the paper's six held-out datasets, in the order of its Fig. 3a table
HELDOUT_NAMES: tuple = ("gisette", "rcv1", "dna", "leukemia", "colon", "duke")

SEED_CPP = r'''
// Plain cyclic coordinate descent along the lambda path (warm starts, residual updates).
#include <cstdio>
#include <cstdint>
#include <cmath>
#include <vector>
int main() {
    int32_t hdr[3];
    if (fread(hdr, sizeof(int32_t), 3, stdin) != 3) return 1;
    const int n = hdr[0], p = hdr[1], K = hdr[2];
    std::vector<double> X((size_t)n * p), y(n), lam(K);
    if (fread(X.data(), sizeof(double), X.size(), stdin) != X.size()) return 1;
    if (fread(y.data(), sizeof(double), n, stdin) != (size_t)n) return 1;
    if (fread(lam.data(), sizeof(double), K, stdin) != (size_t)K) return 1;
    std::vector<double> Xc((size_t)n * p);            // column-major copy
    for (int i = 0; i < n; ++i) for (int j = 0; j < p; ++j) Xc[(size_t)j * n + i] = X[(size_t)i * p + j];
    std::vector<double> xv(p), beta(p, 0.0), r(y), out((size_t)p * K, 0.0);
    for (int j = 0; j < p; ++j) { double s = 0; for (int i = 0; i < n; ++i) s += Xc[(size_t)j*n+i]*Xc[(size_t)j*n+i]; xv[j] = s / n; }
    for (int k = 0; k < K; ++k) {
        for (int it = 0; it < 100000; ++it) {
            double dmax = 0.0;
            for (int j = 0; j < p; ++j) {
                if (xv[j] <= 0) continue;
                const double* c = &Xc[(size_t)j * n];
                double g = 0; for (int i = 0; i < n; ++i) g += c[i] * r[i];
                const double z = g / n + xv[j] * beta[j];
                const double a = std::fabs(z) - lam[k];
                const double nb = a > 0 ? std::copysign(a, z) / xv[j] : 0.0;
                const double d = nb - beta[j];
                if (d != 0.0) { for (int i = 0; i < n; ++i) r[i] -= d * c[i]; beta[j] = nb; dmax = std::fmax(dmax, xv[j] * d * d); }
            }
            if (dmax < 1e-14) break;
        }
        for (int j = 0; j < p; ++j) out[(size_t)k * p + j] = beta[j];
    }
    fwrite(out.data(), sizeof(double), out.size(), stdout);
    return 0;
}
'''


def seed_program() -> str:
    return f"# EVOLVE-BLOCK-START\n\nCPP_CODE = r'''{SEED_CPP}'''\n\nCOMPILE_FLAGS = []\n\n# EVOLVE-BLOCK-END\n"


# ----------------------------------------------------------------------------------- problems
def make_problem(n: int, p: int, gen: str, seed: int) -> tuple[np.ndarray, np.ndarray]:
    """One benchmark instance. ``gaussian``: iid N(0,1) design, p//20 nonzero N(0,1) coefficients;
    ``dense_sol``: the same with p//5 nonzeros; ``sparse``: min(20, p) nonzeros per row, columns scaled to
    unit std, p//100 nonzero coefficients N(0, 4); ``corr_low``/``corr_high``: Toeplitz correlation 0.5/0.9,
    p//20 nonzeros. Noise 0.1 N(0,1) throughout (``numpy.random.RandomState(seed)`` streams)."""
    rng = np.random.RandomState(seed)
    if gen in ("gaussian", "dense_sol"):
        s = max(1, p // (20 if gen == "gaussian" else 5))
        X = rng.randn(n, p)
        w = np.zeros(p)
        idx = rng.choice(p, s, replace=False)      # support first, then values (the benchmark's draw order)
        w[idx] = rng.randn(s)
    elif gen == "sparse":
        nnz = min(20, p)
        rows = np.repeat(np.arange(n), nnz)
        cols = np.array([rng.choice(p, nnz, replace=False) for _ in range(n)]).ravel()
        vals = rng.randn(n * nnz)
        X = np.zeros((n, p))
        np.add.at(X, (rows, cols), vals)
        sd = X.std(axis=0)
        X /= np.where(sd > 1e-8, sd, 1.0)
        s = max(1, p // 100)
        w = np.zeros(p)
        w[rng.choice(p, s, replace=False)] = rng.randn(s) * 2     # Python evaluates the values first
    elif gen in ("corr_low", "corr_high"):
        rho = 0.5 if gen == "corr_low" else 0.9
        idx = np.arange(p)
        L = np.linalg.cholesky(rho ** np.abs(idx[:, None] - idx[None, :]))
        X = rng.randn(n, p) @ L.T
        s = max(1, p // 20)
        w = np.zeros(p)
        w[rng.choice(p, s, replace=False)] = rng.randn(s)
    else:
        raise ValueError(f"unknown generator {gen!r}")
    return X, X @ w + 0.1 * rng.randn(n)


def sklearn_path(X: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The reference (and the paper's "sklearn" row): ``lasso_path(X, y, n_alphas=50, eps=1e-2)``."""
    import warnings

    from sklearn.linear_model import lasso_path

    with warnings.catch_warnings():       # sklearn >= 1.9 deprecates n_alphas (same path as alphas=50)
        warnings.simplefilter("ignore", FutureWarning)
        alphas, coefs, _ = lasso_path(X, y, n_alphas=N_LAMBDA, eps=EPS)
    return np.asarray(alphas), np.asarray(coefs)


def objective(X, y, w, lam) -> float:
    r = y - X @ w
    return float(0.5 / X.shape[0] * (r @ r) + lam * np.abs(w).sum())


def max_gap(X, y, coef: np.ndarray, ref: np.ndarray, alphas) -> float:
    """max_k F_k(coef_k) - F_k(ref_k): the correctness gate passes iff this is <= 1e-6 (and coef is finite)."""
    if coef.shape != ref.shape or not np.all(np.isfinite(coef)):
        return math.inf
    return max(objective(X, y, coef[:, k], a) - objective(X, y, ref[:, k], a) for k, a in enumerate(alphas))


# ------------------------------------------------------------------------------ programs
def parse_program(code: str) -> tuple[str, list[str]]:
    """``CPP_CODE`` and ``COMPILE_FLAGS`` of a candidate file, read as literals (the file is never
    executed: an agent's Python would otherwise run inside the grader)."""
    tree = ast.parse(code)
    found: dict = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) \
                and node.targets[0].id in ("CPP_CODE", "COMPILE_FLAGS"):
            found[node.targets[0].id] = ast.literal_eval(node.value)
    if not isinstance(found.get("CPP_CODE"), str):
        raise ValueError("the program must assign a string literal to CPP_CODE")
    flags = found.get("COMPILE_FLAGS", [])
    if not isinstance(flags, list) or not all(isinstance(f, str) for f in flags):
        raise ValueError("COMPILE_FLAGS must be a list of strings")
    bad = [f for f in flags if not f.startswith(("-f", "-O", "-m", "-D", "-W", "-std="))]
    if bad:
        raise ValueError(f"compile flags not allowed: {bad}")
    return found["CPP_CODE"], flags


def compile_program(cpp: str, flags: Sequence[str], *, eigen_dir: Optional[str] = None,
                    cache_dir: Optional[str] = None, timeout_s: float = 300.0) -> tuple[Optional[str], Optional[str]]:
    """g++ -O3 -march=native -std=c++17 [-I eigen] <flags>; cached by content. Returns (binary, error)."""
    cache = Path(cache_dir or Path(tempfile.gettempdir()) / "rsi_lasso_cpp")
    cache.mkdir(parents=True, exist_ok=True)
    inc = f"-I{eigen_dir}" if eigen_dir else "-I/usr/include/eigen3"
    key = hashlib.sha256((cpp + "\0" + " ".join(flags) + "\0" + inc).encode()).hexdigest()[:16]
    binary = cache / f"lasso_{key}"
    if binary.exists():
        return str(binary), None
    src = cache / f"lasso_{key}.cpp"
    src.write_text(cpp)
    cmd = ["g++", "-O3", "-march=native", "-std=c++17", inc, *flags, str(src), "-o", str(binary) + ".tmp"]
    try:
        rr = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, f"compiler unavailable or timed out: {e}"
    if rr.returncode != 0:
        return None, "C++ compilation failed:\n" + rr.stderr[-1500:]
    os.replace(str(binary) + ".tmp", binary)
    return str(binary), None


def run_binary(binary: str, X: np.ndarray, y: np.ndarray, lambdas, *, timeout_s: float = 300.0,
               threads: int = 1) -> np.ndarray:
    """One solver call (wire format above); raises RuntimeError on a crash or a malformed result."""
    X = np.ascontiguousarray(X, dtype=np.float64)
    n, p = X.shape
    lam = np.ascontiguousarray(lambdas, dtype=np.float64)
    payload = struct.pack("iii", n, p, len(lam)) + X.tobytes() + np.ascontiguousarray(y, np.float64).tobytes() \
        + lam.tobytes()
    env = {**os.environ, "OMP_NUM_THREADS": str(threads), "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1"}
    proc = subprocess.run([binary], input=payload, capture_output=True, timeout=timeout_s, env=env)
    if proc.returncode != 0:
        raise RuntimeError(f"solver crashed (code {proc.returncode}): {proc.stderr.decode(errors='ignore')[:400]}")
    if len(proc.stdout) != p * len(lam) * 8:
        raise RuntimeError(f"output size {len(proc.stdout)} != {p * len(lam) * 8}")
    return np.frombuffer(proc.stdout, dtype=np.float64).reshape((p, len(lam)), order="F")


def timed(fn, reps: int, warmup: int = 0) -> tuple[float, object]:
    """Minimum wall time (ms) over ``reps`` calls after ``warmup`` untimed calls, and the last result."""
    out = None
    for _ in range(warmup):
        out = fn()
    best = math.inf
    for _ in range(reps):
        t0 = time.perf_counter()
        out = fn()
        best = min(best, (time.perf_counter() - t0) * 1e3)
    return best, out


def load_heldout(heldout_dir: Optional[str]) -> tuple[dict, list[str]]:
    """``{name: (X, y)}`` of the held-out datasets present in ``heldout_dir`` and the names missing."""
    out, missing = {}, []
    for name in HELDOUT_NAMES:
        f = Path(heldout_dir) / f"{name}.npz" if heldout_dir else None
        if f is not None and f.exists():
            d = np.load(f)
            out[name] = (np.asarray(d["X"], dtype=np.float64), np.asarray(d["y"], dtype=np.float64).ravel())
        else:
            missing.append(name)
    return out, missing


class SimpleTESLassoDomain(ProgramDomain):
    """The paper's Lasso setting with compiled C++ candidates (see the module docstring)."""

    name = "lasso_cpp"
    program_file = PROGRAM
    entry = "CPP_CODE"

    def __init__(self, *, eigen_dir: Optional[str] = None, heldout_dir: Optional[str] = None,
                 timing_runs: int = 3, heldout_reps: int = 5, threads: int = 1, seed: int = 0,
                 sizes: Optional[Sequence[tuple]] = None, timeout_s: float = 300.0,
                 cache_dir: Optional[str] = None) -> None:
        tasks = [Task("lasso-cpp-search", "search", None, "lasso_cpp"),
                 Task("lasso-cpp-heldout", "holdout", None, "lasso_cpp")]
        super().__init__(TaskSuite(tasks, {"evolve": ["lasso-cpp-search"], "holdout": ["lasso-cpp-heldout"]},
                                   name="lasso_cpp"), sandboxed=True, timeout_s=timeout_s)
        self.eigen_dir, self.heldout_dir = eigen_dir, heldout_dir
        self.timing_runs, self.heldout_reps, self.threads = timing_runs, heldout_reps, threads
        self.seed = seed
        self.sizes = tuple(sizes) if sizes is not None else SIZES
        self.cache_dir = cache_dir

    def describe(self) -> str:
        return ("Lasso regularization path, compiled: define CPP_CODE (C++17 source; Eigen is available) and "
                "optionally COMPILE_FLAGS in solver.py. The binary reads int32 n, p, K, then X (n*p float64, "
                "row-major), y (n) and the decreasing lambda path (K) from stdin, and writes the p*K coefficient "
                "path (float64, column-major) to stdout, minimizing 1/(2n)||y - Xw||^2 + lambda_k ||w||_1 for "
                f"every k. Correctness on fresh instances: F_k(w_k) <= F_k(sklearn lasso_path) + {TOL_OBJECTIVE:g} "
                f"for every k, else score 0. Score = 1 / geometric-mean runtime (ms) over {len(self.sizes)} problem "
                "shapes (fresh random data per timing call). Use double precision throughout.")

    def seed_artifact(self) -> Artifact:
        return Artifact({PROGRAM: seed_program()})

    def binary_of(self, artifact: Artifact) -> tuple[Optional[str], Optional[str]]:
        code = artifact.get(PROGRAM)
        if code is None:
            return None, f"{PROGRAM} missing"
        try:
            cpp, flags = parse_program(code)
        except (SyntaxError, ValueError) as e:
            return None, f"{type(e).__name__}: {e}"
        return compile_program(cpp, flags, eigen_dir=self.eigen_dir, cache_dir=self.cache_dir)

    def problem_seeds(self, eval_seed: int) -> list[dict]:
        rng = np.random.RandomState((1_000_003 * int(self.seed) + int(eval_seed)) % (2 ** 31))
        return [{"timing": [int(rng.randint(1, 2 ** 30)) for _ in range(self.timing_runs)],
                 "correctness": int(rng.randint(1, 2 ** 30))} for _ in self.sizes]

    def evaluate_split(self, artifact: Artifact, which: str = "search", *, eval_seed: int = 0) -> EvalOutcome:
        t0 = time.time()
        binary, err = self.binary_of(artifact)
        if err is not None:
            return EvalOutcome(0.0, True, False, "compile_other", err[:1500], 0, 1, {}, time.time() - t0)
        solve = (lambda X, y, lam: run_binary(binary, X, y, lam, timeout_s=self.timeout_s, threads=self.threads))
        return self.evaluate_solver(solve, which, eval_seed=eval_seed, t0=t0)

    def evaluate_solver(self, solve, which: str = "search", *, eval_seed: int = 0,
                        t0: Optional[float] = None) -> EvalOutcome:
        """Grade any callable ``solve(X, y, lambdas) -> coef (p, K)`` under this protocol (compiled programs,
        and references such as sklearn's own path)."""
        t0 = time.time() if t0 is None else t0
        rows = []
        try:
            if which == "search":
                for (n, p, gen), sd in zip(self.sizes, self.problem_seeds(eval_seed)):
                    ms = math.inf
                    for s in sd["timing"]:
                        X, y = make_problem(n, p, gen, s)
                        alphas, _ = sklearn_path(X, y)
                        t = time.perf_counter()
                        solve(X, y, alphas)
                        ms = min(ms, (time.perf_counter() - t) * 1e3)
                    X, y = make_problem(n, p, gen, sd["correctness"])
                    alphas, ref = sklearn_path(X, y)
                    gap = max_gap(X, y, np.asarray(solve(X, y, alphas), dtype=float), ref, alphas)
                    rows.append({"problem": f"n{n}_p{p}_{gen}", "ms": ms, "gap": gap})
                missing: list[str] = []
            elif which == "holdout":
                data, missing = load_heldout(self.heldout_dir)
                if not data:
                    return EvalOutcome(0.0, True, False, "env_error", "no held-out dataset available", 0, 1,
                                       {"missing": missing}, time.time() - t0)
                for name, (X, y) in data.items():
                    alphas, ref = sklearn_path(X, y)
                    ms, coef = timed(lambda: solve(X, y, alphas), self.heldout_reps, warmup=1)
                    rows.append({"problem": name, "ms": ms,
                                 "gap": max_gap(X, y, np.asarray(coef, dtype=float), ref, alphas)})
            else:
                raise ValueError(f"unknown split {which!r}")
        except subprocess.TimeoutExpired:
            return EvalOutcome(0.0, True, False, "timeout", f"solver timed out after {self.timeout_s}s", 0, 1,
                               {"problems": rows}, time.time() - t0)
        except RuntimeError as e:
            return EvalOutcome(0.0, True, False, "compile_other", str(e)[:800], 0, 1, {"problems": rows},
                               time.time() - t0)
        gm = float(np.exp(np.mean(np.log([max(1e-6, r["ms"]) for r in rows]))))
        worst = max(r["gap"] for r in rows)
        diag = {"problems": rows, "geomean_ms": gm, "max_gap": worst, "missing": missing, "threads": self.threads}
        dt = time.time() - t0
        if not worst <= TOL_OBJECTIVE:
            bad = [r["problem"] for r in rows if not r["gap"] <= TOL_OBJECTIVE]
            return EvalOutcome(0.0, True, False, "correctness",
                               f"objective exceeds sklearn's by {worst:.3g} > {TOL_OBJECTIVE:g} on {bad}", 0, 1, diag, dt)
        return EvalOutcome(1.0 / gm, True, True, "ok", None, len(rows), len(rows), diag, dt)

    def evaluate_program(self, artifact: Artifact, *, seed: int = 0) -> EvalOutcome:
        return self.evaluate_split(artifact, "search", eval_seed=seed)

    def execute(self, artifact, task, *, seed, llm=None):
        from ...core.domain import Execution

        ev = self.evaluate_split(artifact, task.input or "search", eval_seed=seed)
        return Execution(output={"score": ev.score, "fail_class": ev.fail_class, "error": ev.error,
                                 "diagnostics": ev.diagnostics})

    def mock_agent(self, **kw):
        raise NotImplementedError("lasso_cpp needs an agent that writes C++ (e.g. rsi.dream.EditorAgent over an "
                                  "LLM); the offline mock agent works on LassoPathDomain's numpy programs")


__all__ = ["SimpleTESLassoDomain", "SIZES", "HELDOUT_NAMES", "make_problem", "sklearn_path", "max_gap",
           "parse_program", "compile_program", "run_binary", "seed_program", "load_heldout", "timed"]
