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
   ``budget_spent``, ``open``, ``exec``, introspection hooks ...), and no state that
   could outlive an episode (``global``/``nonlocal``, module-level or class objects
   mutated from inside a function, ``lru_cache``/``cache``).

"Replay resets the policy's per-rollout state" [paper:§3 p.5]: every episode (one
frozen world at one beta, or one live search) starts from a FRESH policy namespace.
In-process the module is re-executed per episode; in the subprocess sandbox every
episode runs in a fresh ``fork()`` of a process that has only imported the module,
so nothing a policy writes during one episode - instance, class or module state,
caches, the ``random`` state - can reach the next one (claims audit N1: a policy
that memoised the recorded best per world in a module-level dict walked straight to
it on its next visit and won the beta sweep).
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
FORBIDDEN_ATTRS = {"_t", "_st", "_obs", "tree", "_tree", "hidden", "trace_pool", "__class__", "__bases__", "__base__",
                   "__dict__"}
#: methods that mutate a container in place (a call on a module-level object = cross-episode state)
MUTATING_METHODS = {"append", "extend", "insert", "pop", "popitem", "remove", "clear", "update", "setdefault", "add",
                    "discard", "appendleft", "extendleft", "popleft", "sort", "reverse", "rotate", "difference_update",
                    "intersection_update", "symmetric_difference_update", "__setitem__", "__delitem__", "__iadd__"}
#: calls on the ``random`` module that read or write its process-wide generator state
RANDOM_STATE_CALLS = {"seed", "setstate", "getstate"}
#: memoising decorators (a cache that outlives the call is state that outlives the episode)
CACHE_DECORATORS = {"lru_cache", "cache", "cached_property"}


def _root(node):
    while isinstance(node, (ast.Attribute, ast.Subscript, ast.Starred)):
        node = node.value
    return node


def _bound_names(fn) -> set:
    """Names local to a function or lambda: parameters + every name it (or a nested scope) binds."""
    a = fn.args
    names = {x.arg for x in a.posonlyargs + a.args + a.kwonlyargs}
    names |= {x.arg for x in (a.vararg, a.kwarg) if x is not None}
    body = fn.body if isinstance(fn.body, list) else [fn.body]
    for stmt in body:
        for n in ast.walk(stmt):
            if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
                names.add(n.id)
            elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(n.name)
            elif isinstance(n, ast.arg):
                names.add(n.arg)
            elif isinstance(n, (ast.Import, ast.ImportFrom)):
                names |= {(x.asname or x.name).split(".")[0] for x in n.names}
            elif isinstance(n, ast.ExceptHandler) and n.name:
                names.add(n.name)
    return names


class _EpisodeStateLint(ast.NodeVisitor):
    """Flags code that could carry information from one episode to the next: ``global`` /
    ``nonlocal``, and - inside a function - stores into, or in-place mutation of, anything that
    is not local to that function (a module-level dict, an imported module, a class object),
    stores through ``type(...)``/``super()``, and memoising decorators. Best effort: the real
    guarantee is the fresh namespace per episode (see the module docstring)."""

    def __init__(self) -> None:
        self.scopes: list[set] = []
        self.errors: list[str] = []

    def _err(self, node, what: str) -> None:
        self.errors.append(f"cross-episode state (line {getattr(node, 'lineno', '?')}): {what}; replay resets the "
                           "policy's per-rollout state, so keep per-episode state on self inside solve()")

    def _shared(self, name: str) -> bool:
        return bool(self.scopes) and not any(name in s for s in self.scopes)

    def visit_FunctionDef(self, node) -> None:
        for d in node.decorator_list:
            f = d.func if isinstance(d, ast.Call) else d
            nm = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
            if nm in CACHE_DECORATORS:
                self._err(d, f"memoising decorator @{nm}")
        self.scopes.append(_bound_names(node))
        for stmt in node.body:
            self.visit(stmt)
        self.scopes.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, node) -> None:
        self.scopes.append(_bound_names(node))
        self.visit(node.body)
        self.scopes.pop()

    def visit_Global(self, node) -> None:
        self._err(node, f"'global {', '.join(node.names)}'")

    def visit_Nonlocal(self, node) -> None:
        self._err(node, f"'nonlocal {', '.join(node.names)}'")

    def _target(self, t) -> None:
        if isinstance(t, (ast.Tuple, ast.List)):
            for e in t.elts:
                self._target(e)
            return
        if isinstance(t, ast.Starred):
            self._target(t.value)
            return
        if not self.scopes or not isinstance(t, (ast.Attribute, ast.Subscript)):
            return
        r = _root(t)
        if isinstance(r, ast.Name):
            if self._shared(r.id):
                self._err(t, f"assignment into the non-local object {r.id!r}")
        else:
            self._err(t, "assignment through an expression (e.g. type(self).x or super().x)")

    def visit_Assign(self, node) -> None:
        for t in node.targets:
            self._target(t)
        self.generic_visit(node)

    def visit_AugAssign(self, node) -> None:
        self._target(node.target)
        self.generic_visit(node)

    def visit_AnnAssign(self, node) -> None:
        self._target(node.target)
        self.generic_visit(node)

    def visit_Delete(self, node) -> None:
        for t in node.targets:
            self._target(t)
        self.generic_visit(node)

    def visit_For(self, node) -> None:
        self._target(node.target)
        self.generic_visit(node)

    def visit_With(self, node) -> None:
        for it in node.items:
            if it.optional_vars is not None:
                self._target(it.optional_vars)
        self.generic_visit(node)

    def visit_Call(self, node) -> None:
        f = node.func
        if self.scopes and isinstance(f, ast.Attribute) and f.attr in MUTATING_METHODS:
            r = _root(f.value)
            if isinstance(r, ast.Name) and self._shared(r.id):
                self._err(node, f"in-place mutation {r.id}...{f.attr}() of a non-local object")
        if isinstance(f, ast.Attribute) and f.attr in RANDOM_STATE_CALLS and isinstance(f.value, ast.Name) \
                and f.value.id == "random":
            # the module-level generator is process state: seeding / saving / restoring it can carry a
            # message between episodes of one process (use a local random.Random(seed) instead)
            self._err(node, f"random.{f.attr}() touches the shared module-level random state")
        self.generic_visit(node)


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
    lint = _EpisodeStateLint()
    lint.visit(tree)
    errors.extend(lint.errors)
    errors.extend(_class_state_errors(tree))
    return CheckResult(not errors, sorted(set(errors)), warnings)


_MUTABLE_CALLS = {"dict", "list", "set", "defaultdict", "deque", "Counter", "OrderedDict", "bytearray"}


def _class_state_errors(tree: ast.Module) -> list[str]:
    """A class attribute holding a mutable container that methods mutate through ``self`` without
    ever rebinding it per instance (``cache = {}`` in the class body, ``self.cache[k] = v`` in solve)
    is shared by every instance, i.e. by every episode that uses the class object."""
    out = []
    for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
        mutable = set()
        for st in cls.body:
            if isinstance(st, (ast.Assign, ast.AnnAssign)):
                val = st.value
                is_mut = isinstance(val, (ast.Dict, ast.List, ast.Set, ast.DictComp, ast.ListComp, ast.SetComp)) or (
                    isinstance(val, ast.Call) and getattr(val.func, "id", getattr(val.func, "attr", None)) in _MUTABLE_CALLS)
                if is_mut:
                    tgts = st.targets if isinstance(st, ast.Assign) else [st.target]
                    mutable |= {t.id for t in tgts if isinstance(t, ast.Name)}
        if not mutable:
            continue
        rebound, mutated = set(), {}
        for n in ast.walk(cls):
            if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == "self" \
                    and n.attr in mutable:
                if isinstance(n.ctx, ast.Store):
                    rebound.add(n.attr)
        for n in ast.walk(cls):
            tgt = None
            if isinstance(n, (ast.Assign, ast.AugAssign, ast.AnnAssign, ast.Delete)):
                for t in (n.targets if isinstance(n, (ast.Assign, ast.Delete)) else [n.target]):
                    if isinstance(t, ast.Subscript) or isinstance(n, ast.AugAssign):
                        tgt = t.value if isinstance(t, ast.Subscript) else t
            elif isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in MUTATING_METHODS:
                tgt = n.func.value
            if isinstance(tgt, ast.Attribute) and isinstance(tgt.value, ast.Name) and tgt.value.id == "self" \
                    and tgt.attr in mutable and tgt.attr not in rebound:
                mutated.setdefault(tgt.attr, getattr(n, "lineno", "?"))
        for name, line in sorted(mutated.items()):
            out.append(f"cross-episode state (line {line}): class attribute {cls.name}.{name} is a shared mutable "
                       "container mutated through self; replay resets the policy's per-rollout state, so create "
                       "it per instance (self.{0} = ... in __init__ or solve)".format(name))
    return out


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


_COMPILED: dict[str, types.CodeType] = {}


def _load_class(code: str):
    """Execute the policy module in a NEW namespace and return its class. Code objects are
    immutable, so the compiled module is cached; every call still gets fresh module globals,
    fresh class objects and fresh module-level containers."""
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


class InProcessSession:
    """Runs a trusted policy in this process (still only through the PrefixGuard proxy).

    :meth:`begin_episode` re-executes the module, so an episode never sees module-level,
    class-level or instance state left by an earlier episode ("replay resets the policy's
    per-rollout state"). In-process this cannot isolate state kept in *other* modules
    (``policy_api``, the standard library); :func:`static_check` rejects the obvious routes and the
    subprocess runner forks a fresh process per episode."""

    def __init__(self, code: str) -> None:
        self.code = code
        self._cls = None
        self.load_error: Optional[str] = None
        self._used = False
        self.episodes = 0
        self._load()

    def _load(self) -> None:
        try:
            self._cls = _load_class(self.code)
            self.load_error = None
        except Exception as e:  # noqa: BLE001
            self._cls, self.load_error = None, f"{type(e).__name__}: {e}"
        self._used = False

    def begin_episode(self) -> Optional[str]:
        """Start a new episode in a fresh module namespace (reloaded only if the current one was used)."""
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


class SubprocessSession:
    """One sandboxed child process per policy version; every episode runs in a fresh ``fork()``
    of it (:meth:`begin_episode`), so no state survives from one episode to the next."""

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

    # ---- process management
    def _start(self) -> None:
        # a scrubbed environment (no inherited PYTHON* variable) with a FIXED string-hash seed: an LLM-written
        # policy that iterates a set of cell ids must choose the same batch in every process, or its replay
        # value changes between evaluations (seen in the live validation run sumdiff_live_c). "-s -P" = the
        # isolation of "-I" without "-E", which would also ignore PYTHONHASHSEED.
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
            try:
                os.killpg(self.proc.pid, 9)
            except (ProcessLookupError, PermissionError):
                pass
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
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

    def _end_episode(self) -> None:
        if not self._episode or self.proc is None:
            self._episode = False
            return
        self._episode = False
        try:
            self._send({"cmd": "end"})
            msg = self._recv(self.timeout_s)
        except OSError:
            msg = None
        if msg is None or "closed" not in msg:
            self._kill()

    def begin_episode(self) -> Optional[str]:
        """End the current episode (its forked process exits) and fork a fresh one."""
        if not self._ensure():
            return self.load_error
        self._end_episode()
        if not self._ensure():
            return self.load_error
        try:
            self._send({"cmd": "begin"})
            msg = self._recv(self.timeout_s)
        except OSError:
            msg = None
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
        if not self._episode:
            return self.begin_episode()
        return None

    # ---- API (same as InProcessSession)
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
            if "closed" in msg:      # the episode's process died (e.g. its CPU limit): a policy crash
                self._episode = False
                return SolveOutcome(None, f"policy episode process died (status {msg.get('status')}): "
                                          f"{self._stderr_tail(300)}", guard.violations, guard.batch_errors, wall)
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
                self._end_episode()
            except Exception:  # noqa: BLE001
                pass
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
