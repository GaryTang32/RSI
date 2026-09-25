"""Lasso regularization path: make a correct path solver fast [paper:App.A Problem 1].

The artifact is ``solver.py`` defining ``lasso_path(X, y, lambdas) -> list of coefficient
vectors`` for ``F_k(w) = 1/(2n) ||y - Xw||^2 + lambda_k ||w||_1`` on a decreasing
lambda sequence. The locked evaluator

1. times the solver on the *search* instances (CPU time, best of ``repeats`` runs each -
   CPU time is far less sensitive to other load on a shared machine than wall time);
2. checks correctness on *fresh* instances distinct from the timing ones:
   ``F_k(w~_k) <= F_k(w_ref_k) + 1e-6`` for every k, with ``w_ref`` from a tightly
   converged scikit-learn coordinate-descent reference (numpy fallback when sklearn
   is missing). Any failure scores 0 (``fail_class="correctness"``);
3. scores ``R = (prod_i t_i)^(-1/|I|)``, the inverse geometric-mean runtime in 1/s.

A ``holdout`` task (larger, differently shaped instances) plays the role of the paper's
held-out datasets: ``transfer_report`` / ``evaluate_split`` score a program there.

Mock discovery agent: the seed is a plain cyclic coordinate-descent path solver whose
module constants select mechanisms - FISTA vs coordinate descent, warm starts, strong-
rule screening (with or without the KKT re-check), active-set cycling, a precomputed
Gram matrix, the convergence tolerance. Each attempt edits one mechanism group (the
branch's direction); loose tolerances or screening without KKT checks can fail the
correctness gate (repairable failures).
"""
from __future__ import annotations

import json
import math
import random
import re
import tempfile
import time
from pathlib import Path
from typing import Optional

import numpy as np

from ...core.artifact import Artifact
from ...core.sandbox import run_cmd
from ...core.tasks import Task, TaskSuite
from ...dream.agent import AttemptContext, EvalOutcome, ParametricAgent
from .base import ProgramDomain

PROGRAM = "solver.py"
MECHANISMS = ("algorithm", "warm-start", "screening", "active-set", "gram", "tolerance")
TOL_GATE = 1e-6

SOLVER = '''"""Lasso regularization path solver: F_k(w) = 1/(2n)||y - Xw||^2 + lam_k ||w||_1."""
import numpy as np

ALGO = __ALGO__          # "cd" (coordinate descent) or "fista"
WARM = __WARM__          # warm-start each lambda from the previous solution
SCREEN = __SCREEN__      # sequential strong-rule screening
KKT = __KKT__            # re-check KKT conditions of screened-out features
ACTIVE = __ACTIVE__      # cycle over the active set until convergence, then a full sweep
GRAM = __GRAM__          # precompute X^T X
TOL = __TOL__            # stop when the largest coefficient change < TOL
MAXIT = __MAXIT__


def _soft(z, t):
    return np.sign(z) * np.maximum(np.abs(z) - t, 0.0)


def _cd(X, y, lam, w, cols, G, Xty, sq, n):
    r = None if GRAM else y - X @ w
    for _ in range(MAXIT):
        delta = 0.0
        for j in cols:
            wj = w[j]
            if GRAM:
                rho = (Xty[j] - G[j] @ w) / n + sq[j] * wj
            else:
                rho = X[:, j] @ r / n + sq[j] * wj
            new = _soft(rho, lam) / sq[j] if sq[j] > 0 else 0.0
            if new != wj:
                if not GRAM:
                    r -= X[:, j] * (new - wj)
                w[j] = new
                delta = max(delta, abs(new - wj))
        if delta < TOL:
            break
    return w


def _solve_cd(X, y, lam, w, cols, G, Xty, sq, n):
    if not ACTIVE:
        return _cd(X, y, lam, w, cols, G, Xty, sq, n)
    for _ in range(100):
        w = _cd(X, y, lam, w, cols, G, Xty, sq, n) if not np.any(w[cols]) else w
        act = [j for j in cols if w[j] != 0.0]
        if act:
            w = _cd(X, y, lam, w, act, G, Xty, sq, n)
        before = w.copy()
        w = _cd_one_sweep(X, y, lam, w, cols, G, Xty, sq, n)
        if np.max(np.abs(w - before)) < TOL:
            return w
    return w


def _cd_one_sweep(X, y, lam, w, cols, G, Xty, sq, n):
    global MAXIT
    keep, MAXIT = MAXIT, 1
    try:
        return _cd(X, y, lam, w, cols, G, Xty, sq, n)
    finally:
        MAXIT = keep


def _fista(X, y, lam, w, cols, L, n):
    Xc = X[:, cols]
    x = w[cols].copy()
    z, t = x.copy(), 1.0
    for _ in range(MAXIT):
        g = Xc.T @ (Xc @ z - y) / n
        xn = _soft(z - g / L, lam / L)
        tn = (1 + np.sqrt(1 + 4 * t * t)) / 2
        z = xn + (t - 1) / tn * (xn - x)
        if np.max(np.abs(xn - x)) < TOL:
            x = xn
            break
        x, t = xn, tn
    out = np.zeros_like(w)
    out[cols] = x
    return out


def lasso_path(X, y, lambdas):
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float)
    n, p = X.shape
    sq = (X ** 2).sum(0) / n
    G = X.T @ X if GRAM else None
    Xty = X.T @ y
    L = np.linalg.norm(X, 2) ** 2 / n if ALGO == "fista" else None
    w = np.zeros(p)
    prev_lam = None
    out = []
    for lam in lambdas:
        if not WARM:
            w = np.zeros(p)
        cols = list(range(p))
        if SCREEN and prev_lam is not None:
            grad = np.abs(X.T @ (y - X @ w)) / n
            cols = [j for j in range(p) if grad[j] >= 2 * lam - prev_lam or w[j] != 0.0]
        while True:
            if ALGO == "fista":
                w = _fista(X, y, lam, w, cols, L, n)
            else:
                w = _solve_cd(X, y, lam, w, cols, G, Xty, sq, n)
            if not (SCREEN and KKT and len(cols) < p):
                break
            grad = np.abs(X.T @ (y - X @ w)) / n
            viol = [j for j in range(p) if j not in set(cols) and grad[j] > lam * (1 + 1e-9)]
            if not viol:
                break
            cols = sorted(set(cols) | set(viol))
        out.append(w.tolist())
        prev_lam = lam
    return out
'''

SEED_KNOBS = {"ALGO": "cd", "WARM": False, "SCREEN": False, "KKT": True, "ACTIVE": False, "GRAM": False,
              "TOL": 1e-7, "MAXIT": 3000}


def solver_code(knobs: dict) -> str:
    code = SOLVER
    for k, v in knobs.items():
        code = code.replace(f"__{k}__", repr(v))
    return code


def read_knobs(code: str) -> dict:
    out = {}
    for k in SEED_KNOBS:
        m = re.search(rf"^{k} = (.+?)(\s+#.*)?$", code, re.M)
        if m:
            try:
                out[k] = eval(m.group(1), {})  # noqa: S307 - literal constants of our own template
            except Exception:  # noqa: BLE001
                pass
    return out


# ------------------------------------------------------------------------ instances
def make_instance(n: int, p: int, k: int, rho: float, seed: int, n_lambdas: int = 10, ratio: float = 0.05) -> dict:
    rng = np.random.default_rng(seed)
    C = rho ** np.abs(np.subtract.outer(np.arange(p), np.arange(p)))
    Lc = np.linalg.cholesky(C + 1e-12 * np.eye(p))
    X = rng.standard_normal((n, p)) @ Lc.T
    X /= np.sqrt((X ** 2).mean(0))
    beta = np.zeros(p)
    idx = rng.choice(p, size=min(k, p), replace=False)
    beta[idx] = rng.normal(0, 1, len(idx)) + np.sign(rng.normal(0, 1, len(idx)))
    y = X @ beta + 0.5 * rng.standard_normal(n)
    y = (y - y.mean()) / y.std()
    lam_max = float(np.abs(X.T @ y).max() / n)
    lambdas = list(np.geomspace(lam_max, ratio * lam_max, n_lambdas))
    return {"X": X, "y": y, "lambdas": lambdas}


def objective(X, y, w, lam) -> float:
    n = X.shape[0]
    r = y - X @ w
    return float(r @ r / (2 * n) + lam * np.abs(w).sum())


def reference_objectives(inst: dict) -> list[float]:
    X, y = inst["X"], inst["y"]
    try:
        from sklearn.linear_model import Lasso

        m = Lasso(alpha=inst["lambdas"][0], fit_intercept=False, tol=1e-12, max_iter=200000, warm_start=True,
                  selection="cyclic")
        out = []
        for lam in inst["lambdas"]:
            m.set_params(alpha=lam)
            m.fit(X, y)
            out.append(objective(X, y, m.coef_, lam))
        return out
    except ImportError:  # pragma: no cover - numpy fallback: long ISTA run
        n, p = X.shape
        L = np.linalg.norm(X, 2) ** 2 / n
        w, out = np.zeros(p), []
        for lam in inst["lambdas"]:
            for _ in range(20000):
                w = np.sign(w - X.T @ (X @ w - y) / n / L) * np.maximum(
                    np.abs(w - X.T @ (X @ w - y) / n / L) - lam / L, 0)
            out.append(objective(X, y, w, lam))
        return out


SEARCH_SHAPES = [(120, 250, 10, 0.3), (200, 120, 15, 0.6)]
CHECK_SHAPES = [(60, 90, 6, 0.4), (90, 60, 8, 0.7)]
HOLDOUT_SHAPES = [(300, 400, 20, 0.5), (100, 600, 8, 0.2)]

_HARNESS = r'''
import json, sys, time
import numpy as np
sys.path.insert(0, ".")
data = np.load("instances.npz")
meta = json.loads(open("meta.json").read())
import candidate
res = {"times": [], "objs": []}
for i, m in enumerate(meta["timing"]):
    X, y = data["tX%d" % i], data["ty%d" % i]
    best = None
    for _ in range(meta["repeats"]):
        t0 = time.process_time()
        candidate.lasso_path(X, y, m["lambdas"])
        dt = time.process_time() - t0
        best = dt if best is None else min(best, dt)
    res["times"].append(best)
for i, m in enumerate(meta["check"]):
    X, y = data["cX%d" % i], data["cy%d" % i]
    W = candidate.lasso_path(X, y, m["lambdas"])
    objs = []
    for w, lam in zip(W, m["lambdas"]):
        w = np.asarray(w, dtype=float)
        r = y - X @ w
        objs.append(float(r @ r / (2 * X.shape[0]) + lam * np.abs(w).sum()))
    res["objs"].append(objs)
sys.stdout.write("\n__LASSO__" + json.dumps(res))
'''


class LassoPathDomain(ProgramDomain):
    name = "lasso"
    program_file = PROGRAM
    entry = "lasso_path"

    def __init__(self, *, repeats: int = 3, n_lambdas: int = 10, sandboxed: bool = True, timeout_s: float = 60.0,
                 seed: int = 0) -> None:
        tasks = [Task("lasso-search", "search", None, "lasso"), Task("lasso-heldout", "holdout", None, "lasso")]
        super().__init__(TaskSuite(tasks, {"evolve": ["lasso-search"], "holdout": ["lasso-heldout"]}, name="lasso"),
                         sandboxed=sandboxed, timeout_s=timeout_s)
        self.repeats = repeats
        mk = lambda shapes, s0: [make_instance(n, p, k, rho, s0 + i, n_lambdas) for i, (n, p, k, rho) in
                                 enumerate(shapes)]
        self.sets = {
            "search": {"timing": mk(SEARCH_SHAPES, 1000 + seed), "check": mk(CHECK_SHAPES, 2000 + seed)},
            "holdout": {"timing": mk(HOLDOUT_SHAPES, 3000 + seed), "check": mk(CHECK_SHAPES, 4000 + seed)},
        }
        for s in self.sets.values():
            s["ref"] = [reference_objectives(inst) for inst in s["check"]]
        self._dirs: dict[str, str] = {}

    def describe(self) -> str:
        return ("Lasso regularization path: write solver.py with lasso_path(X, y, lambdas) returning one coefficient "
                "vector per lambda (decreasing sequence) minimizing F_k(w) = 1/(2n)||y - Xw||^2 + lambda_k ||w||_1. "
                f"Correctness gate on fresh instances: F_k(w_k) <= F_k(reference) + {TOL_GATE:g} for every k, else score "
                "0. Score = 1 / geometric-mean runtime (seconds) over the timing instances (higher is better). numpy "
                "is available.")

    def seed_artifact(self) -> Artifact:
        return Artifact({PROGRAM: solver_code(SEED_KNOBS)})

    def directions(self) -> list[str]:
        return list(MECHANISMS)

    # ---- locked evaluator
    def _instance_dir(self, which: str) -> str:
        if which not in self._dirs:
            d = Path(tempfile.mkdtemp(prefix=f"lasso_{which}_"))
            s = self.sets[which]
            arrays = {}
            for i, inst in enumerate(s["timing"]):
                arrays[f"tX{i}"], arrays[f"ty{i}"] = inst["X"], inst["y"]
            for i, inst in enumerate(s["check"]):
                arrays[f"cX{i}"], arrays[f"cy{i}"] = inst["X"], inst["y"]
            np.savez(d / "instances.npz", **arrays)
            (d / "meta.json").write_text(json.dumps({
                "repeats": self.repeats, "timing": [{"lambdas": i["lambdas"]} for i in s["timing"]],
                "check": [{"lambdas": i["lambdas"]} for i in s["check"]]}))
            (d / "_lasso_eval.py").write_text(_HARNESS)
            self._dirs[which] = str(d)
        return self._dirs[which]

    def _measure(self, code: str, which: str) -> tuple[Optional[dict], Optional[str], str]:
        s = self.sets[which]
        if self.sandboxed:
            import shutil

            src = Path(self._instance_dir(which))
            d = Path(tempfile.mkdtemp(prefix="lasso_run_"))
            try:
                for f in ("instances.npz", "meta.json", "_lasso_eval.py"):
                    shutil.copy(src / f, d / f)
                (d / "candidate.py").write_text(code)
                rr = run_cmd([__import__("sys").executable, "_lasso_eval.py"], cwd=d, timeout_s=self.timeout_s,
                             mem_mb=2048)
            finally:
                shutil.rmtree(d, ignore_errors=True)
            if rr.timed_out:
                return None, f"timeout after {self.timeout_s}s", "timeout"
            if "__LASSO__" not in rr.stdout:
                return None, rr.tail(8)[-600:], "compile_other"
            return json.loads(rr.stdout.rsplit("__LASSO__", 1)[1]), None, "ok"
        ns: dict = {"__name__": "candidate"}
        try:
            exec(compile(code, PROGRAM, "exec"), ns)  # noqa: S102 - trusted mock-agent programs
            fn = ns["lasso_path"]
            times = []
            for inst in s["timing"]:
                best = None
                for _ in range(self.repeats):
                    t0 = time.process_time()
                    fn(inst["X"], inst["y"], inst["lambdas"])
                    dt = time.process_time() - t0
                    best = dt if best is None else min(best, dt)
                times.append(best)
            objs = []
            for inst in s["check"]:
                W = fn(inst["X"], inst["y"], inst["lambdas"])
                objs.append([objective(inst["X"], inst["y"], np.asarray(w, float), lam)
                             for w, lam in zip(W, inst["lambdas"])])
        except Exception as e:  # noqa: BLE001
            return None, f"{type(e).__name__}: {e}", "compile_other"
        return {"times": times, "objs": objs}, None, "ok"

    def evaluate_split(self, artifact: Artifact, which: str = "search") -> EvalOutcome:
        code = artifact.get(PROGRAM)
        if code is None:
            return EvalOutcome(0.0, True, False, "compile_other", f"{PROGRAM} missing", 0, 1)
        t0 = time.time()
        res, err, fc = self._measure(code, which)
        dt = time.time() - t0
        if err is not None:
            return EvalOutcome(0.0, True, False, fc, err, 0, 1, {}, dt)
        refs = self.sets[which]["ref"]
        worst = -math.inf
        try:
            for objs, ref in zip(res["objs"], refs):
                if len(objs) != len(ref):
                    return EvalOutcome(0.0, True, False, "correctness", "wrong number of path points", 0, 1, {}, dt)
                worst = max(worst, max(o - r for o, r in zip(objs, ref)))
        except (TypeError, ValueError) as e:
            return EvalOutcome(0.0, True, False, "correctness", f"bad output: {e}", 0, 1, {}, dt)
        times = [max(1e-6, float(t)) for t in res["times"]]
        gm = float(np.exp(np.mean(np.log(times))))
        diag = {"runtime_ms": [round(1000 * t, 3) for t in times], "geomean_ms": round(1000 * gm, 3),
                "max_objective_excess": worst}
        if not worst <= TOL_GATE:
            return EvalOutcome(0.0, True, False, "correctness",
                               f"objective exceeds reference by {worst:.3g} > {TOL_GATE:g}", 0, 1, diag, dt)
        return EvalOutcome(1.0 / gm, True, True, "ok", None, 1, 1, diag, dt)

    def evaluate_program(self, artifact: Artifact, *, seed: int = 0) -> EvalOutcome:
        return self.evaluate_split(artifact, "search")

    def execute(self, artifact, task, *, seed, llm=None):
        ev = self.evaluate_split(artifact, task.input or "search")
        from ...core.domain import Execution

        return Execution(output={"score": ev.score, "fail_class": ev.fail_class, "error": ev.error,
                                 "diagnostics": ev.diagnostics})

    def mock_agent(self, **kw) -> ParametricAgent:
        return ParametricAgent(_mutate, name="lasso-mock", **kw)


def _mutate(parent: Artifact, rng: random.Random, ctx: Optional[AttemptContext]):
    knobs = {**SEED_KNOBS, **read_knobs(parent[PROGRAM])}
    mech = (ctx.direction.get("direction") if ctx and ctx.direction else None) or rng.choice(MECHANISMS)
    if ctx and ctx.attempt > 0 and rng.random() < 0.3:
        mech = rng.choice(MECHANISMS)
    before = dict(knobs)
    if mech == "algorithm":
        knobs["ALGO"] = "fista" if knobs["ALGO"] == "cd" else "cd"
    elif mech == "warm-start":
        knobs["WARM"] = not knobs["WARM"] if rng.random() < 0.3 else True
    elif mech == "screening":
        knobs["SCREEN"] = True if rng.random() < 0.8 else not knobs["SCREEN"]
        knobs["KKT"] = rng.random() < 0.6
    elif mech == "active-set":
        knobs["ACTIVE"] = True if rng.random() < 0.8 else not knobs["ACTIVE"]
    elif mech == "gram":
        knobs["GRAM"] = not knobs["GRAM"]
    else:
        knobs["TOL"] = float(rng.choice([1e-3, 1e-4, 1e-5, 1e-6, 1e-7, 1e-8]))
    changed = {k: v for k, v in knobs.items() if before.get(k) != v}
    text = f"# {mech}: {changed or 'retune'}\n\nMechanism group {mech}; resumes the parent's solver."
    return Artifact({PROGRAM: solver_code(knobs)}), text
