"""Validation commands: policy, runners and the vacuity detector (spec §4.9, §9.2.2-9.2.3).

Evolver validates a gene by running its declared commands (``node <script>``)
in the host repository. The Python port keeps the same safety posture:

* :class:`CommandPolicy` - no shell (``shlex`` argv, metacharacters ``| & ; > <
  ` $`` rejected), an allowlisted executable, blocked eval-style flags
  (``python -c`` is the analogue of ``node -e``), a script argument required
  unless the flag is info-only (``--version`` / ``--help``);
* executors - :class:`SubprocessExecutor` (sandbox subprocess in a scratch copy
  of the workspace, stripped env, timeout) and :class:`InProcessExecutor`
  (simulation worlds only: world-generated scripts run in-process, cached);
* :class:`ValidationRunner` - ``mode="faithful"`` reproduces the local runner's
  quirks (missing scripts are *skipped*, an empty or fully skipped list is *ok*,
  2 retries); ``mode="safe"`` has no silent skips and an **empty list fails**;
* :class:`VacuityDetector` - static lint (empty, info-only, shell no-ops, eval
  flags, missing scripts, print-only or constant-assert scripts by AST) plus the
  discriminative check ``V(W1) = pass and V(W0) = fail`` with optional
  mutation-testing kill rate.
"""
from __future__ import annotations

import ast
import builtins
import hashlib
import io
import json
import re
import shlex
import sys
import tempfile
import threading
import types
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping, Optional, Sequence

from .assets import ValidationReport

SHELL_META = re.compile(r"[|&;<>`$]")
SHELL_NOOPS = {"echo", "true", "exit", ":", "printf", "cat", "ls"}
INFO_FLAGS = ("--version", "-V", "-v", "--help", "-h")


def workspace_digest(files: Mapping[str, str]) -> str:
    return hashlib.sha256(json.dumps(dict(sorted(files.items()))).encode()).hexdigest()


# ----------------------------------------------------------------------------- policy
@dataclass
class PolicyCheck:
    ok: bool
    reason: str = ""
    argv: list[str] = field(default_factory=list)
    script: Optional[str] = None
    info_only: bool = False


@dataclass
class CommandPolicy:
    executables: tuple = ("python", "python3")
    blocked_flags: dict = field(default_factory=lambda: {
        "python": ("-c", "-i", "-m", "-X", "-W"), "python3": ("-c", "-i", "-m", "-X", "-W"),
        "pytest": ("-p", "-c", "--pyargs", "--rootdir", "--confcutdir")})
    internal: tuple = ()          # in-process commands handled by the runner (e.g. "rsi-taskcheck")

    @classmethod
    def faithful(cls) -> "CommandPolicy":
        """Port of Evolver's node-only allowlist: ``python`` only."""
        return cls(executables=("python", "python3"))

    @classmethod
    def safe(cls, internal: Sequence[str] = ("rsi-taskcheck",)) -> "CommandPolicy":
        return cls(executables=("python", "python3", "pytest"), internal=tuple(internal))

    def check(self, cmd: str) -> PolicyCheck:
        cmd = str(cmd or "").strip()
        if not cmd:
            return PolicyCheck(False, "empty command")
        if SHELL_META.search(cmd) or "\\" in cmd:
            return PolicyCheck(False, "shell metacharacters are not allowed")
        try:
            argv = shlex.split(cmd)
        except ValueError as e:
            return PolicyCheck(False, f"parse failed: {e}")
        exe = argv[0]
        if exe in self.internal:
            return PolicyCheck(True, "internal", argv)
        if exe not in self.executables:
            return PolicyCheck(False, f"executable {exe!r} not allowed", argv)
        flags = [a for a in argv[1:] if a.startswith("-")]
        blocked = [f for f in flags if f.split("=")[0] in self.blocked_flags.get(exe, ())]
        if blocked:
            return PolicyCheck(False, f"blocked flag {blocked[0]!r}", argv)
        positional = [a for a in argv[1:] if not a.startswith("-")]
        if not positional:
            if flags and all(f in INFO_FLAGS for f in flags):
                return PolicyCheck(True, "info-only", argv, None, info_only=True)
            return PolicyCheck(False, "a script argument is required", argv)
        script = positional[0]
        if Path(script).is_absolute() or ".." in Path(script).parts:
            return PolicyCheck(False, "script must be a relative path inside the workspace", argv)
        if exe.startswith("python") and not script.endswith(".py"):
            return PolicyCheck(False, "python script must be a .py file", argv)
        return PolicyCheck(True, "ok", argv, script)


# ----------------------------------------------------------------------------- executors
@dataclass
class ExecOutcome:
    exit: int
    stdout: str = ""
    stderr: str = ""


class SubprocessExecutor:
    """Run an argv in a scratch copy of the workspace (``rsi.core.sandbox.run_cmd``)."""

    def __init__(self, timeout_s: float = 60.0, mem_mb: int = 1024, cache: bool = True) -> None:
        self.timeout_s, self.mem_mb, self.cache = timeout_s, mem_mb, cache
        self._memo: dict = {}
        self._lock = threading.Lock()

    def __call__(self, argv: Sequence[str], files: Mapping[str, str]) -> ExecOutcome:
        from rsi.core.sandbox import run_cmd

        key = (tuple(argv), workspace_digest(files))
        if self.cache:
            with self._lock:
                if key in self._memo:
                    return self._memo[key]
        d = Path(tempfile.mkdtemp(prefix="rsi_evomap_val_"))
        for name, text in files.items():
            p = d / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
        exe = list(argv)
        if exe[0] in ("python", "python3"):
            exe[0] = sys.executable
        elif exe[0] == "pytest":
            exe = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *exe[1:]]
        rr = run_cmd(exe, cwd=d, timeout_s=self.timeout_s, mem_mb=self.mem_mb, env={"HOME": str(d), "TMPDIR": str(d)})
        out = ExecOutcome(rr.returncode if not rr.timed_out else 124, rr.stdout[-4000:], rr.stderr[-4000:])
        import shutil
        shutil.rmtree(d, ignore_errors=True)
        if self.cache:
            with self._lock:
                self._memo[key] = out
        return out


_SAFE_IMPORTS = {"math", "re", "json", "sys", "os", "string", "collections", "itertools", "functools", "datetime",
                 "unicodedata", "decimal", "fractions", "random", "time", "typing", "dataclasses"}


class InProcessExecutor:
    """Simulation-only executor: runs world-generated ``python script.py`` checks
    in-process with an importer over the workspace's ``*.py`` files (no
    subprocess, results cached). Not an isolation boundary - never use it for
    code written by a real model; use :class:`SubprocessExecutor` there."""

    def __init__(self) -> None:
        self._memo: dict = {}
        self._lock = threading.Lock()

    def __call__(self, argv: Sequence[str], files: Mapping[str, str]) -> ExecOutcome:
        key = (tuple(argv), workspace_digest(files))
        with self._lock:
            if key in self._memo:
                return self._memo[key]
        out = self._run(list(argv), files)
        with self._lock:
            self._memo[key] = out
        return out

    def _run(self, argv, files) -> ExecOutcome:
        exe = argv[0]
        pos = [a for a in argv[1:] if not a.startswith("-")]
        if not pos:
            if any(a in ("--version", "-V") for a in argv[1:]):
                return ExecOutcome(0, "Python 3.11.0\n")
            return ExecOutcome(0, "usage: python [option] ... [-c cmd | -m mod | file | -] [arg] ...\n")
        script = pos[0]
        if script not in files:
            return ExecOutcome(2, "", f"python: can't open file '{script}': [Errno 2] No such file or directory")
        mods: dict[str, types.ModuleType] = {}

        def load(name):
            if name in mods:
                return mods[name]
            src = files.get(name.replace(".", "/") + ".py")
            if src is None:
                raise ModuleNotFoundError(f"No module named '{name}'")
            m = types.ModuleType(name)
            m.__dict__["__builtins__"] = bi
            mods[name] = m
            exec(compile(src, name + ".py", "exec"), m.__dict__)  # noqa: S102 - simulation-only
            return m

        real_import = builtins.__import__

        def _imp(name, globals=None, locals=None, fromlist=(), level=0):
            top = name.split(".")[0]
            if name.replace(".", "/") + ".py" in files:
                return load(name)
            if top in _SAFE_IMPORTS:
                return real_import(name, globals, locals, fromlist, level)
            raise ModuleNotFoundError(f"No module named '{name}'")

        bi = dict(vars(builtins))
        bi["__import__"] = _imp
        bi["open"] = None
        g = {"__name__": "__main__", "__builtins__": bi}
        buf_o, buf_e = io.StringIO(), io.StringIO()
        code = 0
        try:
            with redirect_stdout(buf_o), redirect_stderr(buf_e):
                exec(compile(files[script], script, "exec"), g)  # noqa: S102 - simulation-only
                if exe == "pytest":
                    for k, v in list(g.items()):
                        if k.startswith("test") and callable(v):
                            v()
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
        except BaseException as e:  # noqa: BLE001 - the check failed
            code = 1
            buf_e.write(f"{type(e).__name__}: {e}\n")
        return ExecOutcome(code, buf_o.getvalue()[-4000:], buf_e.getvalue()[-4000:])


# ----------------------------------------------------------------------------- runner
@dataclass
class ValidationResult:
    ok: bool                         # the runner's verdict (faithful: empty / all skipped = ok)
    report: ValidationReport
    n_run: int = 0
    n_passed: int = 0
    n_skipped: int = 0
    blocked: bool = False

    @property
    def component(self) -> float:
        """Composite-score validation component (§4.10)."""
        if self.n_run == 0:
            return 0.5 if self.ok else 0.0
        return self.n_passed / self.n_run


class ValidationRunner:
    def __init__(self, policy: Optional[CommandPolicy] = None, executor: Optional[Callable] = None, *,
                 mode: str = "faithful", retries: Optional[int] = None,
                 internal: Optional[dict[str, Callable]] = None) -> None:
        self.mode = mode
        self.policy = policy or (CommandPolicy.faithful() if mode == "faithful" else CommandPolicy.safe())
        self.executor = executor or SubprocessExecutor()
        self.retries = (2 if mode == "faithful" else 0) if retries is None else retries
        self.internal = dict(internal or {})
        self._n = 0

    def run(self, specs: Sequence[str], files: Mapping[str, str], *, gene_id: str = "", context=None,
            signer: str = "local", report_id: Optional[str] = None) -> ValidationResult:
        self._n += 1
        rows: list[dict] = []
        ok, blocked = True, False
        n_run = n_pass = n_skip = 0
        if not specs and self.mode != "faithful":
            ok = False
            rows.append({"command": "", "ok": False, "exit": None, "stdout": "", "stderr": "empty validation list"})
        for cmd in specs:
            pc = self.policy.check(cmd)
            if not pc.ok:
                rows.append({"command": cmd, "ok": False, "exit": None, "blocked": True, "stdout": "",
                             "stderr": f"BLOCKED: {pc.reason}"})
                ok, blocked = False, True
                n_run += 1
                break
            if pc.argv and pc.argv[0] in self.internal:
                code, out = self.internal[pc.argv[0]](pc.argv, files, context)
                res = ExecOutcome(code, out)
            else:
                if pc.script and pc.script not in files:
                    if self.mode == "faithful":
                        rows.append({"command": cmd, "ok": True, "exit": None, "skipped": True, "stdout": "",
                                     "stderr": "Skipping validation command (script not in repoRoot)"})
                        n_skip += 1
                        continue
                res = self.executor(pc.argv, files)
                attempt = 0
                while res.exit != 0 and attempt < self.retries:
                    attempt += 1
                    res = self.executor(pc.argv, files)
            n_run += 1
            good = res.exit == 0
            rows.append({"command": cmd, "ok": good, "exit": res.exit, "stdout": res.stdout[-4000:],
                         "stderr": res.stderr[-4000:]})
            if good:
                n_pass += 1
            else:
                ok = False
                break          # first failure stops the run
        overall = bool(n_run) and all(r.get("ok") for r in rows if not r.get("skipped"))
        rep = ValidationReport(id=report_id or f"vr_{gene_id or 'x'}_{self._n:06d}", gene_id=gene_id, commands=rows,
                               overall_ok=overall, signer=signer, meta={"runner_mode": self.mode, "runner_ok": ok})
        return ValidationResult(ok, rep.stamp(), n_run, n_pass, n_skip, blocked)


# ----------------------------------------------------------------------------- vacuity
def _script_findings(src: str) -> list[str]:
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return ["syntax_error"]
    asserts = [n for n in ast.walk(tree) if isinstance(n, ast.Assert)]
    raises = [n for n in ast.walk(tree) if isinstance(n, ast.Raise)]
    exits = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            f = n.func
            name = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else "")
            if name in ("exit", "_exit", "quit"):
                arg = n.args[0] if n.args else None
                if arg is None or (isinstance(arg, ast.Constant) and arg.value in (0, None)):
                    continue
                exits.append(n)
    compares = [n for n in ast.walk(tree) if isinstance(n, ast.Compare)]
    if not asserts and not raises and not exits and not compares:
        return ["print_only"]

    def constant(node) -> bool:
        return not any(isinstance(x, (ast.Name, ast.Call, ast.Attribute, ast.Subscript)) for x in ast.walk(node))

    real_asserts = [a for a in asserts if not constant(a.test)]
    if asserts and not real_asserts and not raises and not exits:
        return ["constant_assert"]
    return []


@dataclass
class DiscriminativeResult:
    pass_before: bool
    pass_after: bool
    kill_rate: Optional[float] = None
    n_mutants: int = 0

    @property
    def discriminative(self) -> bool:
        return self.pass_after and not self.pass_before

    def to_dict(self) -> dict:
        return {"pass_before": self.pass_before, "pass_after": self.pass_after, "discriminative": self.discriminative,
                "kill_rate": self.kill_rate, "n_mutants": self.n_mutants}


@dataclass
class VacuityVerdict:
    vacuous: bool
    reasons: list[str] = field(default_factory=list)
    lint: dict = field(default_factory=dict)          # command -> findings
    discriminative: Optional[DiscriminativeResult] = None

    def to_dict(self) -> dict:
        return {"vacuous": self.vacuous, "reasons": self.reasons, "lint": self.lint,
                "discriminative": self.discriminative.to_dict() if self.discriminative else None}


_MUT_OPS = [(r"\+", "-"), (r"(?<![<>=!])-(?!=)", "+"), (r"<=", "<"), (r">=", ">"), (r"(?<![<>=!])<(?!=)", "<="),
            (r"(?<![<>=!])>(?!=)", ">="), (r"==", "!="), (r"!=", "=="), (r"\bTrue\b", "False"), (r"\bFalse\b", "True"),
            (r"\breturn\s+(?!None\b)(.+)$", "return None")]


def make_mutants(before: Mapping[str, str], after: Mapping[str, str], max_n: int = 6,
                 exclude: Sequence[str] = ()) -> list[dict[str, str]]:
    """Mutation-testing mutants of the changed/added lines of ``after`` (Python files other than
    ``exclude``, i.e. not the validation scripts themselves)."""
    out: list[dict[str, str]] = []
    seen = set()
    for name in sorted(after):
        if not name.endswith(".py") or after.get(name) == before.get(name) or name in exclude:
            continue
        old = set((before.get(name) or "").splitlines())
        lines = after[name].splitlines()
        for i, line in enumerate(lines):
            if line in old or not line.strip() or line.strip().startswith("#"):
                continue
            for pat, rep in _MUT_OPS:
                new = re.sub(pat, rep, line, count=1)
                if new != line and (name, i, new) not in seen:
                    seen.add((name, i, new))
                    mutated = list(lines)
                    mutated[i] = new
                    out.append({**after, name: "\n".join(mutated) + "\n"})
                    if len(out) >= max_n:
                        return out
    return out


class VacuityDetector:
    """Static lint + discriminative before/after check (+ mutation kill rate)."""

    def __init__(self, runner: Optional[ValidationRunner] = None, *, lint_policy: str = "any",
                 min_kill_rate: float = 0.0, n_mutants: int = 6) -> None:
        self.runner = runner or ValidationRunner(mode="safe")
        self.lint_policy = lint_policy
        self.min_kill_rate = min_kill_rate
        self.n_mutants = n_mutants

    def lint(self, specs: Sequence[str], files: Mapping[str, str]) -> dict[str, list[str]]:
        if not specs:
            return {"": ["empty_list"]}
        out: dict[str, list[str]] = {}
        for cmd in specs:
            f: list[str] = []
            try:
                argv = shlex.split(str(cmd))
            except ValueError:
                argv = []
            exe = argv[0] if argv else ""
            if exe in SHELL_NOOPS:
                f.append("shell_noop")
            elif exe in self.runner.policy.internal:
                pass
            else:
                pc = self.runner.policy.check(cmd)
                if pc.info_only:
                    f.append("info_only")
                elif not pc.ok:
                    f.append("eval_flag" if "blocked flag" in pc.reason else "not_allowed")
                elif pc.script:
                    if pc.script not in files:
                        f.append("missing_script")
                    elif pc.script.endswith(".py"):
                        f.extend(_script_findings(files[pc.script]))
            out[str(cmd)] = f
        return out

    def lint_vacuous(self, specs: Sequence[str], files: Mapping[str, str]) -> tuple[bool, dict]:
        lint = self.lint(specs, files)
        flagged = [bool(v) for v in lint.values()]
        vac = any(flagged) if self.lint_policy == "any" else all(flagged)
        return vac, lint

    def with_tests(self, specs: Sequence[str], before: Mapping[str, str], after: Mapping[str, str]) -> dict:
        """W0 with the validation scripts of W1: the *new* tests run against the *old* code
        (otherwise a check script introduced by the change would 'fail before' just by being absent)."""
        w0 = dict(before)
        for cmd in specs:
            pc = self.runner.policy.check(cmd)
            if pc.ok and pc.script and pc.script in after:
                w0[pc.script] = after[pc.script]
        return w0

    def discriminate(self, specs: Sequence[str], before: Mapping[str, str], after: Mapping[str, str], *,
                     mutants: bool = True, context=None) -> DiscriminativeResult:
        before = self.with_tests(specs, before, after)
        # W0 also means "without the gene": internal task checks (rsi-taskcheck) read the gene from the context
        ctx0 = {**context, "gene": None} if isinstance(context, dict) else context
        pb = self.runner.run(specs, before, context=ctx0).ok if specs else False
        pa = self.runner.run(specs, after, context=context).ok if specs else False
        res = DiscriminativeResult(pb, pa)
        if mutants and pa and not pb:
            scripts = [pc.script for pc in map(self.runner.policy.check, specs) if pc.ok and pc.script]
            ms = make_mutants(before, after, self.n_mutants, exclude=scripts)
            if ms:
                killed = sum(1 for m in ms if not self.runner.run(specs, m, context=context).ok)
                res.kill_rate, res.n_mutants = killed / len(ms), len(ms)
        return res

    def verdict(self, specs: Sequence[str], before: Mapping[str, str], after: Mapping[str, str], *,
                use_lint: bool = True, use_discriminative: bool = True, mutants: bool = True,
                context=None) -> VacuityVerdict:
        reasons: list[str] = []
        lint: dict = {}
        if use_lint:
            vac, lint = self.lint_vacuous(specs, after)
            if vac:
                reasons += sorted({k for v in lint.values() for k in v})
        disc = None
        if use_discriminative and (not reasons or not use_lint):
            disc = self.discriminate(specs, before, after, mutants=mutants, context=context)
            if disc.pass_before:
                reasons.append("passes_before_change")
            if not disc.pass_after:
                reasons.append("fails_after_change")
            if disc.kill_rate is not None and disc.kill_rate < self.min_kill_rate:
                reasons.append(f"kill_rate {disc.kill_rate:.2f} < {self.min_kill_rate}")
        return VacuityVerdict(bool(reasons), reasons, lint, disc)
