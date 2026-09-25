"""The static check of a policy module (PolicyStaticCheck of the spec), run before a policy is
ever executed [paper:App.B.2 L2:131-143 hard constraints]:

* ``NAME`` + class present, ``solve`` and ``plan_grid`` overridden, ``plan_grid`` never returns None;
* allow-listed imports only; no forbidden names (``best_so_far``, ``budget_spent``, ``open``,
  ``exec``, introspection hooks ...) or attributes (hidden tree, proxy internals, ``__class__`` ...);
* no state that could outlive an episode ("replay resets the policy's per-rollout state" [paper:§3
  p.5]; claims audit N1): ``global`` / ``nonlocal``, stores into or in-place mutation of module-level,
  imported or shared class objects from inside a function, ``random.seed/setstate/getstate``, and
  memoising decorators. Best effort: the guarantee is the fresh policy namespace per episode
  (:mod:`rsi.dream.guard`); this lint rejects the obvious routes early, with a message the policy
  developer can act on.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field

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
