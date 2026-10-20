"""Static check of a policy module before it ever runs [paper:App.B.2 L2:131-143], incl. cross-episode state."""
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
#: in-place container mutations (on a module-level object they carry state across episodes)
MUTATING_METHODS = {"append", "extend", "insert", "pop", "popitem", "remove", "clear", "update", "setdefault", "add",
                    "discard", "appendleft", "extendleft", "popleft", "sort", "reverse", "rotate", "difference_update",
                    "intersection_update", "symmetric_difference_update", "__setitem__", "__delitem__", "__iadd__"}
RANDOM_STATE_CALLS = {"seed", "setstate", "getstate"}
CACHE_DECORATORS = {"lru_cache", "cache", "cached_property"}
_MUTABLE_CALLS = {"dict", "list", "set", "defaultdict", "deque", "Counter", "OrderedDict", "bytearray"}


def _root(node):
    while isinstance(node, (ast.Attribute, ast.Subscript, ast.Starred)):
        node = node.value
    return node


def _bound_names(fn) -> set:
    """Names local to a function or lambda: parameters + every name it (or a nested scope) binds."""
    a = fn.args
    names = {x.arg for x in a.posonlyargs + a.args + a.kwonlyargs}
    names |= {x.arg for x in (a.vararg, a.kwarg) if x is not None}
    for stmt in fn.body if isinstance(fn.body, list) else [fn.body]:
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
    """Flags routes for state to outlive an episode: ``global``/``nonlocal``, stores into or mutation of non-local
    objects from a function, stores into imported objects, and memoising decorators."""

    def __init__(self) -> None:
        self.scopes: list[set] = []
        self.errors: list[str] = []
        self.imported: set = set()  # module-level imports outlive the policy module, so they are shared state

    def visit_Import(self, node) -> None:
        if not self.scopes:
            self.imported |= {(a.asname or a.name).split(".")[0] for a in node.names}

    def visit_ImportFrom(self, node) -> None:
        if not self.scopes:
            self.imported |= {a.asname or a.name for a in node.names if a.name != "*"}

    def _into_import(self, r) -> bool:
        return not self.scopes and isinstance(r, ast.Name) and r.id in self.imported

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
        elif isinstance(t, ast.Starred):
            self._target(t.value)
        elif isinstance(t, (ast.Attribute, ast.Subscript)):
            r = _root(t)
            if self._into_import(r):
                self._err(t, f"assignment into the imported object {r.id!r} at module level (it outlives the "
                             "policy module, e.g. policy_api.memo = {})")
            elif self.scopes and not isinstance(r, ast.Name):
                self._err(t, "assignment through an expression (e.g. type(self).x or super().x)")
            elif self.scopes and self._shared(r.id):
                self._err(t, f"assignment into the non-local object {r.id!r}")

    def visit_Assign(self, node) -> None:
        for t in getattr(node, "targets", None) or [node.target]:
            self._target(t)
        self.generic_visit(node)

    visit_Delete = visit_AugAssign = visit_AnnAssign = visit_For = visit_Assign

    def visit_With(self, node) -> None:
        for it in node.items:
            if it.optional_vars is not None:
                self._target(it.optional_vars)
        self.generic_visit(node)

    def visit_Call(self, node) -> None:
        f = node.func
        if isinstance(f, ast.Attribute) and f.attr in MUTATING_METHODS:
            r = _root(f.value)
            if self.scopes and isinstance(r, ast.Name) and self._shared(r.id):
                self._err(node, f"in-place mutation {r.id}...{f.attr}() of a non-local object")
            elif self._into_import(r):
                self._err(node, f"in-place mutation {r.id}...{f.attr}() of an imported object at module level")
        if isinstance(f, ast.Attribute) and f.attr in RANDOM_STATE_CALLS and isinstance(f.value, ast.Name) \
                and f.value.id == "random":
            self._err(node, f"random.{f.attr}() touches the shared module-level random state")
        self.generic_visit(node)


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
        bad = (e.text or "").strip()  # quoted so the developer sees the cause, not just a line number
        return CheckResult(False, [f"syntax error: {e}" + (f" - offending line: {bad[:120]!r}" if bad else "")])
    names = [node.value.value for node in tree.body
             if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "NAME" for t in node.targets)
             and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)]
    name = names[-1] if names else None
    if name is None:
        errors.append('missing NAME = "OptimalPolicy"')
    cls = {n.name: n for n in tree.body if isinstance(n, ast.ClassDef)}.get(name or "OptimalPolicy")
    if cls is None:
        errors.append(f"class {name or 'OptimalPolicy'} not defined at module level")
    else:
        methods = {n.name: n for n in cls.body if isinstance(n, ast.FunctionDef)}
        errors += [f"{cls.name}.{m} is not overridden" for m in ("solve", "plan_grid") if m not in methods]
        pg = methods.get("plan_grid")
        if pg is not None:
            if any(isinstance(r, ast.Return) and (r.value is None or (isinstance(r.value, ast.Constant)
                                                                      and r.value.value is None))
                   for r in ast.walk(pg)):
                errors.append("plan_grid returns None on some path")
            if pg.body and not isinstance(pg.body[-1], (ast.Return, ast.Raise, ast.If)):
                warnings.append("plan_grid may fall off the end (implicit None)")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            errors += [f"import of {a.name!r} is not allowed" for a in node.names
                       if a.name.split(".")[0] not in ALLOWED_IMPORTS]
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] not in ALLOWED_IMPORTS:
                errors.append(f"import from {node.module!r} is not allowed")
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            errors.append(f"forbidden name {node.id!r}")
        elif isinstance(node, ast.Attribute) and node.attr in (FORBIDDEN_NAMES | FORBIDDEN_ATTRS):
            errors.append(f"forbidden attribute .{node.attr}")
    lint = _EpisodeStateLint()
    lint.visit(tree)
    errors += lint.errors + _class_state_errors(tree)
    return CheckResult(not errors, sorted(set(errors)), warnings)


def _class_state_errors(tree: ast.Module) -> list[str]:
    """A mutable class attribute mutated through ``self`` and never rebound per instance is shared by all episodes."""
    out = []
    for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
        mutable = set()
        for st in cls.body:
            if not isinstance(st, (ast.Assign, ast.AnnAssign)):
                continue
            val = st.value
            if isinstance(val, (ast.Dict, ast.List, ast.Set, ast.DictComp, ast.ListComp, ast.SetComp)) or (
                    isinstance(val, ast.Call) and getattr(val.func, "id", getattr(val.func, "attr", None))
                    in _MUTABLE_CALLS):
                mutable |= {t.id for t in (st.targets if isinstance(st, ast.Assign) else [st.target])
                            if isinstance(t, ast.Name)}
        if not mutable:
            continue

        def self_attr(x) -> bool:
            return isinstance(x, ast.Attribute) and isinstance(x.value, ast.Name) and x.value.id == "self" \
                and x.attr in mutable

        rebound = {n.attr for n in ast.walk(cls) if self_attr(n) and isinstance(n.ctx, ast.Store)}
        mutated = {}
        for n in ast.walk(cls):
            tgt = None
            if isinstance(n, (ast.Assign, ast.AugAssign, ast.AnnAssign, ast.Delete)):
                for t in (n.targets if isinstance(n, (ast.Assign, ast.Delete)) else [n.target]):
                    if isinstance(t, ast.Subscript) or isinstance(n, ast.AugAssign):
                        tgt = t.value if isinstance(t, ast.Subscript) else t
            elif isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in MUTATING_METHODS:
                tgt = n.func.value
            if self_attr(tgt) and tgt.attr not in rebound:
                mutated.setdefault(tgt.attr, getattr(n, "lineno", "?"))
        for name, line in sorted(mutated.items()):
            out.append(f"cross-episode state (line {line}): class attribute {cls.name}.{name} is a shared mutable "
                       "container mutated through self; replay resets the policy's per-rollout state, so create "
                       "it per instance (self.{0} = ... in __init__ or solve)".format(name))
    return out
