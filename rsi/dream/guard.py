"""PrefixGuard: exploration policies run as hot-swappable *code* behind a firewall.

"Strategies must decide using only what they have revealed so far. They can't
peek at unrevealed scores or use known best answers" [doc]; Listing 2's hard
constraints [paper:App.B.2 L2:131-143]. Three layers:

1. :class:`PrefixGuard` - the parent-side endpoint. The policy only ever holds a
   :class:`policy_api.QuestionProxy`; every request (``reset``, ``probe``) goes
   through this endpoint, which returns *copies* of the revealed prefix, the legal
   sets and structural meta - never the hidden tree, ``best_so_far``,
   ``budget_spent`` or scores of unrevealed cells. Batch legality (<= W cells, no
   duplicates, legal before the call, no parent+child) is validated here;
   violations and illegal batches disqualify the episode.
2. :class:`SubprocessRunner` - the policy module runs in a separate Python process
   (scratch dir with only ``method.py`` + ``policy_api.py``, scrubbed env, CPU /
   memory rlimits, and after import the open-file limit is lowered so the policy
   cannot open any file, e.g. a trace pool). The hidden world never enters that
   process: the parent answers JSON requests over pipes. Per-episode timeout.
   :class:`InProcessRunner` is the fast path for trusted built-in templates.
3. :func:`static_check` - AST lint before a policy is ever run: ``NAME`` + class
   present, ``solve`` and ``plan_grid`` overridden, ``plan_grid`` never returns
   None, allow-listed imports only, no forbidden names (``best_so_far``,
   ``budget_spent``, ``open``, ``exec``, introspection hooks ...).
"""
from __future__ import annotations

import ast
import json
import os
import resource
import select
import shutil
import subprocess
import sys
import tempfile
import time
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..core.sandbox import SAFE_ENV_KEYS
from . import policy_api
from .policy_api import BatchError, GridPlan, GridPlanningContext, GuardViolation, QuestionProxy, SimResult

API_PATH = Path(policy_api.__file__)

ALLOWED_IMPORTS = {"policy_api", "math", "statistics", "collections", "itertools", "functools", "heapq",
                   "bisect", "dataclasses", "typing", "__future__", "random", "operator", "enum", "numbers"}
FORBIDDEN_NAMES = {"best_so_far", "budget_spent", "open", "exec", "eval", "compile", "__import__", "globals",
                   "locals", "vars", "breakpoint", "input", "getattr", "setattr", "delattr", "__self__",
                   "__globals__", "__closure__", "__code__", "__builtins__", "__subclasses__", "__mro__",
                   "f_back", "f_locals", "f_globals", "gi_frame"}
#: attribute names a policy may never touch (bare local variables with these names are fine)
FORBIDDEN_ATTRS = {"_t", "_st", "_obs", "tree", "_tree", "hidden", "trace_pool"}


# =========================================================================== static check
@dataclass
class CheckResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.ok


def static_check(code: str) -> CheckResult:
    """Lint a policy module before it is run (PolicyStaticCheck of the spec)."""
    errors, warnings = [], []
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        # quote the offending line: "invalid syntax (line 188)" alone made the live-run developer
        # invent a cause in its repair round (the line was a stray ``` left by the reply parser)
        bad = (e.text or "").strip()
        return CheckResult(False, [f"syntax error: {e}" + (f" - offending line: {bad[:120]!r}" if bad else "")])
    name = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "NAME" for t in node.targets):
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                name = node.value.value
    if name is None:
        errors.append('missing NAME = "OptimalPolicy"')
    classes = {n.name: n for n in tree.body if isinstance(n, ast.ClassDef)}
    cls = classes.get(name or "OptimalPolicy")
    if cls is None:
        errors.append(f"class {name or 'OptimalPolicy'} not defined at module level")
    else:
        methods = {n.name: n for n in cls.body if isinstance(n, ast.FunctionDef)}
        for m in ("solve", "plan_grid"):
            if m not in methods:
                errors.append(f"{cls.name}.{m} is not overridden")
        pg = methods.get("plan_grid")
        if pg is not None:
            for r in ast.walk(pg):
                if isinstance(r, ast.Return) and (r.value is None or (isinstance(r.value, ast.Constant)
                                                                      and r.value.value is None)):
                    errors.append("plan_grid returns None on some path")
                    break
            if pg.body and not isinstance(pg.body[-1], (ast.Return, ast.Raise, ast.If)):
                warnings.append("plan_grid may fall off the end (implicit None)")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] not in ALLOWED_IMPORTS:
                    errors.append(f"import of {a.name!r} is not allowed")
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] not in ALLOWED_IMPORTS:
                errors.append(f"import from {node.module!r} is not allowed")
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            errors.append(f"forbidden name {node.id!r}")
        elif isinstance(node, ast.Attribute) and node.attr in (FORBIDDEN_NAMES | FORBIDDEN_ATTRS):
            errors.append(f"forbidden attribute .{node.attr}")
    return CheckResult(not errors, sorted(set(errors)), warnings)


# ============================================================================ endpoint
class PrefixGuard:
    """Parent-side firewall around a Question (replay or online)."""

    def __init__(self, question, *, strict: bool = True) -> None:
        self.q = question
        self.strict = strict
        self.violations: list[str] = []
        self.batch_errors: list[str] = []
        self.n_requests = 0

    def state(self) -> dict:
        legal = self.q.legal_actions()
        # structural meta (branch, attempt, parent, tags such as the direction label) of every
        # legal AND every revealed cell - "decisions may use ... structural meta" [App.B.2 L2:53-55];
        # never the meta of an unrevealed, illegal cell
        cells = list(legal) + [c for c in self.q.observed() if c not in set(legal)]
        meta = {c: self.q.meta(c).to_dict() for c in cells}
        return {"legal_actions": legal, "legal_roots": self.q.legal_roots(), "opened": self.q.opened_branches(),
                "meta": meta, "baseline_score": self.q.baseline_score, "max_parallelism": self.q.max_parallelism,
                "done": self.q.is_done(), "round": self.q.k}

    def handle(self, op: str, **kw) -> dict:
        self.n_requests += 1
        if op == "reset":
            if self.q.k > 0 or self.q.N > 0:
                # "replay resets the policy's per-rollout state" at episode START only: a reset after
                # probing would let a policy explore everything, remember it, reset the counters and
                # then walk straight to the best recorded cell (peeking through its own memory)
                self.violations.append("reset() after probing (an episode may only be reset before its "
                                       "first probe)")
                return {"reset_denied": True, **self.state()}
            self.q.reset()
            return {"reset": True, **self.state()}
        if op == "probe":
            cells = [str(c) for c in kw.get("cells", [])]
            try:
                obs = self.q.probe_batch(cells)
            except BatchError as e:
                self.batch_errors.append(str(e))
                return {"batch_error": str(e), **self.state()}
            return {"revealed": [o.to_dict() for o in obs], **self.state()}
        if op == "violation":
            self.violations.append(str(kw.get("what")))
            return {}
        self.violations.append(f"unknown request {op!r}")
        return {}

    def transport(self, op: str, **kw) -> dict:
        # JSON round trip: the policy never shares a mutable object with the question
        return json.loads(json.dumps(self.handle(op, **kw), default=float))


# ============================================================================= runners
@dataclass
class SolveOutcome:
    result: Optional[SimResult]
    error: Optional[str] = None           # policy exception / timeout / crash
    violations: list[str] = field(default_factory=list)
    batch_errors: list[str] = field(default_factory=list)
    cpu_s: float = 0.0

    @property
    def disqualified(self) -> bool:
        return bool(self.violations or self.batch_errors or self.error)


def _load_class(code: str):
    sys.modules.setdefault("policy_api", policy_api)
    mod = types.ModuleType("dream_policy_method")
    mod.__dict__["__file__"] = "method.py"
    exec(compile(code, "method.py", "exec"), mod.__dict__)  # noqa: S102 - trusted template path
    name = mod.__dict__.get("NAME", "OptimalPolicy")
    cls = mod.__dict__.get(name)
    if cls is None:
        raise ImportError(f"policy module defines no class {name!r}")
    return cls


class InProcessSession:
    """Runs a trusted policy in this process (still only through the PrefixGuard proxy)."""

    def __init__(self, code: str) -> None:
        self.code = code
        self._cls = None
        self.load_error: Optional[str] = None
        try:
            self._cls = _load_class(code)
        except Exception as e:  # noqa: BLE001
            self.load_error = f"{type(e).__name__}: {e}"

    def plan_grid(self, config: dict, context: GridPlanningContext) -> tuple[Optional[GridPlan], Optional[str]]:
        if self._cls is None:
            return None, self.load_error
        try:
            plan = self._cls(dict(config)).plan_grid(GridPlanningContext.from_dict(context.to_dict()))
        except Exception as e:  # noqa: BLE001
            return None, f"plan_grid raised {type(e).__name__}: {e}"
        if plan is None:
            return None, "plan_grid returned None"
        try:
            return GridPlan.from_dict(plan.to_dict() if hasattr(plan, "to_dict") else dict(plan)), None
        except Exception as e:  # noqa: BLE001
            return None, f"plan_grid returned an invalid plan: {e}"

    def solve(self, config: dict, question, *, budget: Optional[int] = None, strict: bool = True,
              unguarded: bool = False) -> SolveOutcome:
        if self._cls is None:
            return SolveOutcome(None, self.load_error)
        guard = PrefixGuard(question, strict=strict)
        proxy = question if unguarded else QuestionProxy(guard.transport)
        t0 = time.process_time()
        try:
            res = self._cls(dict(config)).solve(proxy, budget)
            err = None
        except GuardViolation as e:
            res, err = None, None
            if not guard.violations:
                guard.violations.append(str(e))
        except BatchError as e:
            res, err = None, None
            if not guard.batch_errors:
                guard.batch_errors.append(str(e))
        except Exception as e:  # noqa: BLE001
            res, err = None, f"{type(e).__name__}: {e}"
        cpu = time.process_time() - t0
        if res is not None and not isinstance(res, SimResult):
            res = SimResult.from_dict(res.to_dict()) if hasattr(res, "to_dict") else SimResult()
        return SolveOutcome(res, err, list(guard.violations), list(guard.batch_errors), cpu)

    def close(self) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


_CHILD = r'''
import json, os, sys, traceback, resource
_proto = os.fdopen(os.dup(1), "w")
os.dup2(2, 1)
sys.stdout = sys.stderr
sys.path.insert(0, os.getcwd())
def send(o):
    _proto.write(json.dumps(o, default=str) + "\n"); _proto.flush()
def recv():
    line = sys.stdin.readline()
    if not line:
        os._exit(0)
    return json.loads(line)
import policy_api
try:
    import method
    cls = getattr(method, getattr(method, "NAME", "OptimalPolicy"))
except BaseException:
    send({"fatal": traceback.format_exc(limit=5)}); os._exit(1)
def _is_open(fd):
    try:
        os.fstat(fd)
        return True
    except OSError:
        return False
try:  # firewall: from here on no new file descriptor can be opened (lowest free fd >= the limit)
    free = next(fd for fd in range(4096) if not _is_open(fd))
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    resource.setrlimit(resource.RLIMIT_NOFILE, (free, hard))
except Exception:
    pass
send({"ready": True})
def transport(op, **kw):
    send({"req": op, **kw})
    m = recv()
    return m.get("resp", {})
while True:
    cmd = recv(); c = cmd.get("cmd")
    if c == "exit":
        break
    try:
        pol = cls(cmd.get("config") or {})
        if c == "plan_grid":
            plan = pol.plan_grid(policy_api.GridPlanningContext.from_dict(cmd["context"]))
            send({"result": plan.to_dict() if plan is not None else None})
        elif c == "solve":
            res = pol.solve(policy_api.QuestionProxy(transport), cmd.get("budget"))
            send({"result": res.to_dict() if hasattr(res, "to_dict") else None})
    except policy_api.GuardViolation as e:
        send({"violation": str(e)})
    except policy_api.BatchError as e:
        send({"batch_error": str(e)})
    except BaseException:
        send({"exception": traceback.format_exc(limit=6)})
'''


def _preexec(cpu_s: int, mem_mb: int):
    def apply():
        os.setsid()
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_s, cpu_s + 5))
        b = mem_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (b, b))
    return apply


class SubprocessSession:
    """One sandboxed child process per policy version, reused across episodes."""

    def __init__(self, code: str, *, timeout_s: float = 30.0, cpu_s: int = 900, mem_mb: int = 1024) -> None:
        self.code = code
        self.timeout_s = timeout_s
        self.cpu_s, self.mem_mb = cpu_s, mem_mb
        self.dir = Path(tempfile.mkdtemp(prefix="dream_policy_"))
        (self.dir / "method.py").write_text(code)
        shutil.copy(API_PATH, self.dir / "policy_api.py")
        (self.dir / "_child.py").write_text(_CHILD)
        self.proc: Optional[subprocess.Popen] = None
        self.load_error: Optional[str] = None
        self._buf = b""
        self._start()

    # ---- process management
    def _start(self) -> None:
        env = {k: os.environ[k] for k in SAFE_ENV_KEYS if k in os.environ and k != "PYTHONPATH"}
        env.update({"OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "PYTHONDONTWRITEBYTECODE": "1"})
        self._err = open(self.dir / "stderr.txt", "wb")
        self.proc = subprocess.Popen([sys.executable, "-I", "_child.py"], cwd=self.dir, env=env,
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self._err, bufsize=0,
                                     preexec_fn=_preexec(self.cpu_s, self.mem_mb))
        self._buf = b""
        msg = self._recv(self.timeout_s)
        if msg is None or "ready" not in msg:
            self.load_error = (msg or {}).get("fatal") or self._stderr_tail() or "policy process failed to start"
            self._kill()

    def _stderr_tail(self, n: int = 1500) -> str:
        try:
            return (self.dir / "stderr.txt").read_text(errors="replace")[-n:]
        except OSError:
            return ""

    def _kill(self) -> None:
        if self.proc is not None:
            try:
                os.killpg(self.proc.pid, 9)
            except (ProcessLookupError, PermissionError):
                pass
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            self.proc = None

    def _send(self, obj: dict) -> None:
        assert self.proc is not None and self.proc.stdin is not None
        self.proc.stdin.write((json.dumps(obj, default=float) + "\n").encode())
        self.proc.stdin.flush()

    def _recv(self, timeout: float) -> Optional[dict]:
        assert self.proc is not None and self.proc.stdout is not None
        fd = self.proc.stdout.fileno()
        deadline = time.time() + timeout
        while b"\n" not in self._buf:
            rem = deadline - time.time()
            if rem <= 0:
                return None
            r, _, _ = select.select([fd], [], [], rem)
            if not r:
                return None
            chunk = os.read(fd, 1 << 16)
            if not chunk:
                return None
            self._buf += chunk
        line, _, self._buf = self._buf.partition(b"\n")
        try:
            return json.loads(line.decode())
        except json.JSONDecodeError:
            return {"exception": f"protocol error: {line[:200]!r}"}

    def _ensure(self) -> bool:
        if self.proc is None and self.load_error is None:
            self._start()
        return self.proc is not None

    # ---- API (same as InProcessSession)
    def plan_grid(self, config: dict, context: GridPlanningContext) -> tuple[Optional[GridPlan], Optional[str]]:
        if not self._ensure():
            return None, self.load_error
        self._send({"cmd": "plan_grid", "config": config, "context": context.to_dict()})
        msg = self._recv(self.timeout_s)
        if msg is None:
            self._kill()
            return None, "plan_grid timed out"
        if msg.get("result") is None:
            return None, msg.get("exception") or msg.get("violation") or "plan_grid returned None"
        try:
            return GridPlan.from_dict(msg["result"]), None
        except Exception as e:  # noqa: BLE001
            return None, f"invalid plan: {e}"

    def solve(self, config: dict, question, *, budget: Optional[int] = None, strict: bool = True,
              unguarded: bool = False) -> SolveOutcome:
        if unguarded:
            raise ValueError("the subprocess sandbox cannot run a policy unguarded")
        if not self._ensure():
            return SolveOutcome(None, self.load_error)
        guard = PrefixGuard(question, strict=strict)
        t0 = time.time()
        policy_s = 0.0          # time spent waiting on the POLICY (excludes parent-side work such as the
        #                         agent calls an online probe triggers - those can take minutes)
        self._send({"cmd": "solve", "config": config, "budget": budget})
        while True:
            rem = self.timeout_s - policy_s
            w0 = time.time()
            msg = self._recv(max(0.01, rem))
            policy_s += time.time() - w0
            if msg is None:
                self._kill()
                return SolveOutcome(None, f"policy timed out after {self.timeout_s}s or died: {self._stderr_tail(300)}",
                                    guard.violations, guard.batch_errors, policy_s)
            if "req" in msg:
                op = msg.pop("req")
                self._send({"resp": json.loads(json.dumps(guard.handle(op, **msg), default=float))})
                continue
            wall = policy_s
            if "result" in msg:
                res = SimResult.from_dict(msg["result"]) if isinstance(msg["result"], dict) else SimResult()
                return SolveOutcome(res, None, guard.violations, guard.batch_errors, wall)
            if "violation" in msg:
                v = guard.violations or [msg["violation"]]
                return SolveOutcome(None, None, v, guard.batch_errors, wall)
            if "batch_error" in msg:
                return SolveOutcome(None, None, guard.violations, guard.batch_errors or [msg["batch_error"]], wall)
            return SolveOutcome(None, msg.get("exception") or msg.get("fatal") or "unknown child reply",
                                guard.violations, guard.batch_errors, wall)

    def close(self) -> None:
        if self.proc is not None:
            try:
                self._send({"cmd": "exit"})
                self.proc.wait(timeout=2)
            except Exception:  # noqa: BLE001
                pass
            self._kill()
        try:
            self._err.close()
        except Exception:  # noqa: BLE001
            pass
        shutil.rmtree(self.dir, ignore_errors=True)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def __del__(self):  # pragma: no cover - best effort cleanup
        try:
            self.close()
        except Exception:  # noqa: BLE001
            pass


class InProcessRunner:
    name = "inprocess"

    def session(self, code: str) -> InProcessSession:
        return InProcessSession(code)


class SubprocessRunner:
    name = "subprocess"

    def __init__(self, timeout_s: float = 30.0, cpu_s: int = 900, mem_mb: int = 1024) -> None:
        self.timeout_s, self.cpu_s, self.mem_mb = timeout_s, cpu_s, mem_mb

    def session(self, code: str) -> SubprocessSession:
        return SubprocessSession(code, timeout_s=self.timeout_s, cpu_s=self.cpu_s, mem_mb=self.mem_mb)


def get_runner(kind: str = "subprocess", **kw):
    if kind == "subprocess":
        return SubprocessRunner(**kw)
    if kind == "inprocess":
        return InProcessRunner()
    raise ValueError(f"unknown sandbox {kind!r}")
