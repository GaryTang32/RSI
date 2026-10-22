"""PrefixGuard and policy runners: policy code sees only the revealed prefix, each episode in a fresh namespace."""
from __future__ import annotations

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
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..core.sandbox import SAFE_ENV_KEYS
from . import policy_api
from .lint import CheckResult, static_check  # noqa: F401 (re-exported)
from .policy_api import BatchError, GridPlan, GridPlanningContext, GuardViolation, QuestionProxy, SimResult

API_PATH = Path(policy_api.__file__)


class PrefixGuard:
    """Parent-side endpoint: answers ``reset``/``probe`` with copies of the prefix, legal sets and structural meta."""

    def __init__(self, question, *, strict: bool = True) -> None:
        self.q = question
        self.strict = strict
        self.violations: list[str] = []
        self.batch_errors: list[str] = []
        self.n_requests = 0

    def state(self) -> dict:
        legal = self.q.legal_actions()
        legal_set = set(legal)
        # meta of every legal and every revealed cell, never of an unrevealed illegal one [App.B.2 L2:53-55]
        meta = {c: self.q.meta(c).to_dict() for c in list(legal) + [c for c in self.q.observed() if c not in legal_set]}
        return {"legal_actions": legal, "legal_roots": self.q.legal_roots(), "opened": self.q.opened_branches(),
                "meta": meta, "baseline_score": self.q.baseline_score, "max_parallelism": self.q.max_parallelism,
                "done": self.q.is_done(), "round": self.q.k}

    def handle(self, op: str, **kw) -> dict:
        self.n_requests += 1
        if op == "reset":
            if self.q.k > 0 or self.q.N > 0:
                # a reset after probing would let a policy explore, remember and walk straight to the best cell
                self.violations.append("reset() after probing (an episode may only be reset before its "
                                       "first probe)")
                return {"reset_denied": True, **self.state()}
            self.q.reset()
            return {"reset": True, **self.state()}
        if op == "probe":
            try:
                obs = self.q.probe_batch([str(c) for c in kw.get("cells", [])])
            except BatchError as e:
                self.batch_errors.append(str(e))
                return {"batch_error": str(e), **self.state()}
            return {"revealed": [o.to_dict() for o in obs], **self.state()}
        self.violations.append(str(kw.get("what")) if op == "violation" else f"unknown request {op!r}")
        return {}

    def transport(self, op: str, **kw) -> dict:
        # JSON round trip: the policy never shares a mutable object with the question
        return json.loads(json.dumps(self.handle(op, **kw), default=float))


@dataclass
class SolveOutcome:
    result: Optional[SimResult]
    error: Optional[str] = None  # policy exception / timeout / crash
    violations: list[str] = field(default_factory=list)
    batch_errors: list[str] = field(default_factory=list)
    cpu_s: float = 0.0

    @property
    def disqualified(self) -> bool:
        return bool(self.violations or self.batch_errors or self.error)


_COMPILED: dict[str, types.CodeType] = {}


def _load_class(code: str):
    """Execute the policy module in a new namespace (compiled code is cached) and return its class."""
    sys.modules.setdefault("policy_api", policy_api)
    compiled = _COMPILED.get(code)
    if compiled is None:
        compiled = compile(code, "method.py", "exec")
        if len(_COMPILED) > 256:
            _COMPILED.clear()
        _COMPILED[code] = compiled
    mod = types.ModuleType("dream_policy_method")
    mod.__dict__["__file__"] = "method.py"
    exec(compiled, mod.__dict__)  # noqa: S102 - trusted template path
    name = mod.__dict__.get("NAME", "OptimalPolicy")
    cls = mod.__dict__.get(name)
    if cls is None:
        raise ImportError(f"policy module defines no class {name!r}")
    return cls


class _Session:
    def close(self) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class InProcessSession(_Session):
    """Runs a trusted policy in-process behind the PrefixGuard proxy, re-executing the module per episode."""

    def __init__(self, code: str) -> None:
        self.code = code
        self._cls = None
        self.load_error: Optional[str] = None
        self._used = False
        self.episodes = 0
        self._load()

    def _load(self) -> None:
        try:
            self._cls, self.load_error = _load_class(self.code), None
        except Exception as e:  # noqa: BLE001
            self._cls, self.load_error = None, f"{type(e).__name__}: {e}"
        self._used = False

    def begin_episode(self) -> Optional[str]:
        """Start an episode in a fresh module namespace (reloaded only if the current one was used)."""
        if self._used:
            self._load()
        self.episodes += 1
        return self.load_error

    def plan_grid(self, config: dict, context: GridPlanningContext) -> tuple[Optional[GridPlan], Optional[str]]:
        if self._cls is None:
            return None, self.load_error
        self._used = True
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
        self._used = True
        guard = PrefixGuard(question, strict=strict)
        proxy = question if unguarded else QuestionProxy(guard.transport)
        t0 = time.process_time()
        res, err = None, None
        try:
            res = self._cls(dict(config)).solve(proxy, budget)
        except GuardViolation as e:
            if not guard.violations:
                guard.violations.append(str(e))
        except BatchError as e:
            if not guard.batch_errors:
                guard.batch_errors.append(str(e))
        except Exception as e:  # noqa: BLE001
            err = f"{type(e).__name__}: {e}"
        cpu = time.process_time() - t0
        if res is not None and not isinstance(res, SimResult):
            res = SimResult.from_dict(res.to_dict()) if hasattr(res, "to_dict") else SimResult()
        return SolveOutcome(res, err, list(guard.violations), list(guard.batch_errors), cpu)


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
def episode():
    # one EPISODE runs in a fork() of this process: the policy module exactly as it was right after
    # import, so nothing written during an earlier episode (module / class / instance state, caches,
    # the random state) exists here - "replay resets the policy's per-rollout state"
    send({"episode": os.getpid()})
    while True:
        cmd = recv(); c = cmd.get("cmd")
        if c in ("end", "exit"):
            os._exit(0)
        try:
            pol = cls(cmd.get("config") or {})
            if c == "plan_grid":
                plan = pol.plan_grid(policy_api.GridPlanningContext.from_dict(cmd["context"]))
                send({"result": plan.to_dict() if plan is not None else None})
            elif c == "solve":
                res = pol.solve(policy_api.QuestionProxy(transport), cmd.get("budget"))
                send({"result": res.to_dict() if hasattr(res, "to_dict") else None})
            else:
                send({"exception": "unknown command %r" % (c,)})
        except policy_api.GuardViolation as e:
            send({"violation": str(e)})
        except policy_api.BatchError as e:
            send({"batch_error": str(e)})
        except BaseException:
            send({"exception": traceback.format_exc(limit=6)})
while True:
    cmd = recv(); c = cmd.get("cmd")
    if c == "exit":
        break
    if c != "begin":
        send({"exception": "command %r outside an episode" % (c,)})
        continue
    sys.stderr.flush()
    pid = os.fork()
    if pid == 0:
        try:
            episode()
        finally:
            os._exit(0)
    _, status = os.waitpid(pid, 0)
    send({"closed": True, "status": status})
'''


def _preexec(cpu_s: int, mem_mb: int):
    def apply():
        os.setsid()
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_s, cpu_s + 5))
        b = mem_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (b, b))
    return apply


class SubprocessSession(_Session):
    """A sandboxed child process per policy version; every episode runs in a fresh ``fork()`` of it."""

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
        self._episode = False
        self.episodes = 0
        self._start()

    def _start(self) -> None:
        # no inherited PYTHON* variable, but a FIXED hash seed so set iteration order (and so the chosen batch) is
        # the same in every process; "-s -P" isolates like "-I" without "-E", which would ignore PYTHONHASHSEED
        env = {k: os.environ[k] for k in SAFE_ENV_KEYS if k in os.environ and not k.startswith("PYTHON")}
        env.update({"OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "PYTHONDONTWRITEBYTECODE": "1",
                    "PYTHONHASHSEED": "0", "PYTHONNOUSERSITE": "1"})
        self._err = open(self.dir / "stderr.txt", "wb")
        self.proc = subprocess.Popen([sys.executable, "-s", "-P", "_child.py"], cwd=self.dir, env=env,
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self._err, bufsize=0,
                                     preexec_fn=_preexec(self.cpu_s, self.mem_mb))
        self._buf = b""
        self._episode = False
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
            with suppress(ProcessLookupError, PermissionError):
                os.killpg(self.proc.pid, 9)
            with suppress(subprocess.TimeoutExpired):
                self.proc.wait(timeout=5)
            self.proc = None
        self._episode = False

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
            if rem <= 0 or not select.select([fd], [], [], rem)[0]:
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

    def _request(self, cmd: str) -> Optional[dict]:
        try:
            self._send({"cmd": cmd})
            return self._recv(self.timeout_s)
        except OSError:
            return None

    def _end_episode(self) -> None:
        if self._episode and self.proc is not None:
            self._episode = False
            msg = self._request("end")
            if msg is None or "closed" not in msg:
                self._kill()
        self._episode = False

    def begin_episode(self) -> Optional[str]:
        """End the current episode (its forked process exits) and fork a fresh one."""
        if not self._ensure():
            return self.load_error
        self._end_episode()
        if not self._ensure():
            return self.load_error
        msg = self._request("begin")
        if msg is None or "episode" not in msg:
            self._kill()
            return "could not start a policy episode: " + str((msg or {}).get("exception") or self._stderr_tail(300))
        self._episode = True
        self.episodes += 1
        return None

    def _ready(self) -> Optional[str]:
        """An open episode to talk to (one is started if none is open)."""
        if not self._ensure():
            return self.load_error or "policy process failed to start"
        return None if self._episode else self.begin_episode()

    def plan_grid(self, config: dict, context: GridPlanningContext) -> tuple[Optional[GridPlan], Optional[str]]:
        err = self._ready()
        if err:
            return None, err
        self._send({"cmd": "plan_grid", "config": config, "context": context.to_dict()})
        msg = self._recv(self.timeout_s)
        if msg is None:
            self._kill()
            return None, "plan_grid timed out"
        if "closed" in msg:
            self._episode = False
            return None, f"policy episode process died (status {msg.get('status')}): {self._stderr_tail(300)}"
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
        err = self._ready()
        if err:
            return SolveOutcome(None, err)
        guard = PrefixGuard(question, strict=strict)
        # time spent waiting on the policy only (parent-side agent calls during an online probe excluded)
        policy_s = 0.0
        self._send({"cmd": "solve", "config": config, "budget": budget})
        while True:
            w0 = time.time()
            msg = self._recv(max(0.01, self.timeout_s - policy_s))
            policy_s += time.time() - w0
            if msg is None:
                self._kill()
                return SolveOutcome(None, f"policy timed out after {self.timeout_s}s or died: {self._stderr_tail(300)}",
                                    guard.violations, guard.batch_errors, policy_s)
            if "req" in msg:
                op = msg.pop("req")
                self._send({"resp": json.loads(json.dumps(guard.handle(op, **msg), default=float))})
                continue
            if "closed" in msg:  # the episode's process died (e.g. its CPU limit): a policy crash
                self._episode = False
                return SolveOutcome(None, f"policy episode process died (status {msg.get('status')}): "
                                          f"{self._stderr_tail(300)}", guard.violations, guard.batch_errors, policy_s)
            if "result" in msg:
                res = SimResult.from_dict(msg["result"]) if isinstance(msg["result"], dict) else SimResult()
                return SolveOutcome(res, None, guard.violations, guard.batch_errors, policy_s)
            if "violation" in msg:
                return SolveOutcome(None, None, guard.violations or [msg["violation"]], guard.batch_errors, policy_s)
            if "batch_error" in msg:
                return SolveOutcome(None, None, guard.violations, guard.batch_errors or [msg["batch_error"]], policy_s)
            return SolveOutcome(None, msg.get("exception") or msg.get("fatal") or "unknown child reply",
                                guard.violations, guard.batch_errors, policy_s)

    def close(self) -> None:
        if self.proc is not None:
            with suppress(Exception):
                self._end_episode()
        if self.proc is not None:
            with suppress(Exception):
                self._send({"cmd": "exit"})
                self.proc.wait(timeout=2)
            self._kill()
        with suppress(Exception):
            self._err.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def __del__(self):  # pragma: no cover - best effort cleanup
        with suppress(Exception):
            self.close()


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
