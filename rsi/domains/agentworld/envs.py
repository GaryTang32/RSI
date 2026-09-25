"""AgentWorld environment families: small multi-step agent tasks with large tool outputs.

Four families, all CPU-only and deterministic, each with a verifier returning a
score in [0, 1] (fraction of subtasks correctly completed). They mirror the
blueprint's MiniAgentWorld (spec B9.2) and differ where it matters for
harness-efficiency research - *where the decisive evidence sits in long outputs*:

=============  =========================================================  =================================
family         task                                                       decisive evidence
=============  =========================================================  =================================
``repofix``    fix seeded constant bugs so failing pytest tests pass      pytest short summary (tail)
               (issue -> fix style, regression tests)
``buildfix``   fix misspelled identifiers so ``make`` succeeds            FIRST compiler error (head/middle),
                                                                          followed by cascades
``logtriage``  write report.txt with counts / timestamps from a log,      the whole log (counts); checker
               validated by ``make check`` (verifier-first style)         lists failed items
``datalookup`` answer questions about JSONL records, checked by a         "line N is wrong" at the TOP of a
               pytest checker (held-out family: other formats)            long captured-output dump
=============  =========================================================  =================================

The workspace is virtual (``files``). ``tool_bash`` understands ``cat``, ``ls``,
``grep [-c] [-n] [-m N]``, ``head/tail -n N``, ``sed -n 'a,bp'``, ``wc -l``,
pipelines of those, plus family commands (``pytest`` [-q --tb=line] [target],
``make`` [target|check]). Paths under ``/.solpi/`` and ``/tmp/`` read the
runtime's private store (EPR archives, full bash outputs). Like Pi's bash tool,
outputs over 50 KB / 2,000 lines are cut to the tail with a note and the full
output saved under ``/tmp/pi-bash-<n>.log``.
"""
from __future__ import annotations

import hashlib
import json
import random
import re
import shlex
from dataclasses import dataclass, field
from typing import Any, Optional

from ...solpi.runtime import ToolResult

PI_MAX_BYTES = 50 * 1024
PI_MAX_LINES = 2000


@dataclass
class Subtask:
    id: str
    goal: str
    key: str                 # what identifies it in outputs (module / file / item / line number)
    data: dict = field(default_factory=dict)   # hidden ground truth for the verifier


class Env:
    family = "env"
    style = "per_subtask"            # per_subtask | batch

    def __init__(self, task_id: str, seed: int, n_subtasks: int) -> None:
        self.task_id = task_id
        self.rng = random.Random(f"agentworld|{task_id}|{seed}")
        self.files: dict[str, str] = {"docs/GUIDE.md": project_guide(random.Random(f"guide|{task_id}|{seed}"))}
        self.subtasks: list[Subtask] = []
        self.n_bash = 0
        self.log: list[str] = []

    # ---- workspace
    def read_file(self, path: str) -> Optional[str]:
        return self.files.get(path.lstrip("./") if not path.startswith("/") else path)

    def _read_any(self, path: str, rt=None) -> Optional[str]:
        if path.startswith(("/.solpi/", "/tmp/")):
            return (rt.store.get(path) if rt is not None else None)
        return self.files.get(path[2:] if path.startswith("./") else path)

    def tool_read(self, path: str, rt=None) -> ToolResult:
        text = self._read_any(path, rt)
        if text is None:
            return ToolResult(f"ENOENT: no such file: {path}", is_error=True)
        return ToolResult(text)

    def tool_write(self, path: str, content: str, rt=None) -> ToolResult:
        self.files[path] = content
        self.log.append(f"write {path}")
        return ToolResult(f"Successfully wrote {len(content.encode())} bytes to {path}")

    def tool_edit(self, path: str, old: str, new: str, rt=None) -> ToolResult:
        text = self.files.get(path)
        if text is None:
            return ToolResult(f"ENOENT: no such file: {path}", is_error=True)
        n = text.count(old) if old else 0
        if n == 0:
            return ToolResult(f"Could not find the exact text in {path}. The old text must match exactly.",
                              is_error=True)
        if n > 1:
            return ToolResult(f"Found {n} occurrences of the text in {path}. The text must be unique.",
                              is_error=True)
        self.files[path] = text.replace(old, new, 1)
        self.log.append(f"edit {path}")
        return ToolResult(f"Successfully replaced text in {path}.")

    def tool_bash(self, command: str, rt=None) -> ToolResult:
        self.n_bash += 1
        try:
            out, code = self.shell(command, rt)
        except Exception as e:  # noqa: BLE001
            out, code = f"bash: {e}", 2
        details: dict[str, Any] = {"exit_code": code}
        lines = out.split("\n")
        if len(out.encode()) > PI_MAX_BYTES or len(lines) > PI_MAX_LINES:
            path = f"/tmp/pi-bash-{self.task_id.replace('/', '_')}-{self.n_bash}.log"
            if rt is not None:
                rt.store[path] = out
            kept, used = [], 0
            for ln in reversed(lines):
                if used + len(ln) + 1 > PI_MAX_BYTES or len(kept) >= PI_MAX_LINES:
                    break
                kept.append(ln)
                used += len(ln) + 1
            out = "\n".join(reversed(kept)) + f"\n\n[Showing last {len(kept)} of {len(lines)} lines. Full output: {path}]"
            details["fullOutputPath"] = path
        if code != 0:
            return ToolResult(out + f"\n\nCommand exited with code {code}", is_error=True, details=details)
        return ToolResult(out, details=details)

    # ---- shell
    def shell(self, command: str, rt=None) -> tuple[str, int]:
        cmd = command.strip()
        cmd = re.sub(r"\s*2>&1", "", cmd)
        parts = [p.strip() for p in cmd.split("|")]
        out, code = self.run_one(parts[0], rt)
        for filt in parts[1:]:
            out, fcode = self.filter(filt, out)
            if fcode:
                code = fcode if code == 0 else code
        return out, code

    def run_one(self, cmd: str, rt=None) -> tuple[str, int]:
        try:
            argv = shlex.split(cmd)
        except ValueError as e:
            return f"bash: syntax error: {e}", 2
        if not argv:
            return "", 0
        prog = argv[0]
        if prog == "cat":
            chunks = []
            for p in argv[1:]:
                t = self._read_any(p, rt)
                if t is None:
                    return f"cat: {p}: No such file or directory", 1
                chunks.append(t)
            return "".join(chunks), 0
        if prog == "ls":
            d = argv[1].rstrip("/") + "/" if len(argv) > 1 else ""
            names = sorted({f[len(d):].split("/")[0] for f in self.files if f.startswith(d)})
            return "\n".join(names), 0
        if prog in ("grep", "head", "tail", "wc", "sed") and len(argv) >= 2 and not argv[-1].startswith("-"):
            src = self._read_any(argv[-1], rt) if prog != "sed" or "/" in argv[-1] or "." in argv[-1] else None
            if src is not None:
                return self.filter(" ".join(shlex.quote(a) for a in argv[:-1]), src)
        return self.family_command(argv, rt)

    def filter(self, filt: str, text: str) -> tuple[str, int]:
        argv = shlex.split(filt)
        prog, args = argv[0], argv[1:]
        lines = text.split("\n")
        if prog in ("head", "tail"):
            n = 10
            if "-n" in args:
                n = int(args[args.index("-n") + 1])
            elif args and args[0].startswith("-") and args[0][1:].isdigit():
                n = int(args[0][1:])
            return "\n".join(lines[:n] if prog == "head" else lines[-n:]), 0
        if prog == "wc":
            return str(text.count("\n") + (0 if text.endswith("\n") else 1)), 0
        if prog == "sed":
            m = re.search(r"(\d+),(\d+)p", filt)
            if not m:
                return "sed: unsupported expression", 1
            a, b = int(m.group(1)), int(m.group(2))
            return "\n".join(lines[a - 1:b]), 0
        if prog == "grep":
            count = "-c" in args
            number = "-n" in args
            fixed = "-F" in args
            limit = None
            pat = None
            i = 0
            while i < len(args):
                a = args[i]
                if a == "-m":
                    limit = int(args[i + 1])
                    i += 2
                    continue
                if a.startswith("-m") and a[2:].isdigit():
                    limit = int(a[2:])
                elif not a.startswith("-") and pat is None:
                    pat = a
                i += 1
            if pat is None:
                return "grep: missing pattern", 2
            rx = re.compile(re.escape(pat) if fixed else pat)
            hits = [(k + 1, ln) for k, ln in enumerate(lines) if rx.search(ln)]
            if limit is not None:
                hits = hits[:limit]
            if count:
                return str(len(hits)), 0 if hits else 1
            return "\n".join(f"{k}:{ln}" if number else ln for k, ln in hits), 0 if hits else 1
        return f"bash: {prog}: command not found", 127

    # ---- to implement
    def statement(self) -> str:
        raise NotImplementedError

    def family_command(self, argv: list[str], rt=None) -> tuple[str, int]:
        return f"bash: {argv[0]}: command not found", 127

    def verify(self) -> float:
        raise NotImplementedError

    def subtask_ok(self, st: Subtask) -> bool:
        raise NotImplementedError


def project_guide(r: random.Random) -> str:
    """A long, generic contributor guide (~20-28 KB): the kind of large successful output that
    agents read early and then replay in every later request."""
    topics = ["build", "testing", "logging", "configuration", "deployment", "code style", "reviews", "releases",
              "observability", "security", "performance", "migrations"]
    out = ["# Contributor guide", ""]
    for t in r.sample(topics, len(topics)):
        out += [f"## {t.title()}", ""]
        for k in range(r.randint(14, 22)):
            out.append(f"- Rule {k + 1}: when changing {t}, keep modules small, document public functions, run the "
                       f"relevant checks locally and describe user-visible effects in the changelog ({r.randint(1, 99)}).")
        out.append("")
    return "\n".join(out)


def _h(*parts) -> int:
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:8], 16)


WORDS = ["alpha", "beta", "delta", "sigma", "omega", "kappa", "theta", "gamma", "lambda", "zeta", "rho", "tau"]
MODULES = ["pricing", "inventory", "shipping", "billing", "reports", "auth", "search", "cache", "ledger", "quota",
           "orders", "catalog", "profile", "notify", "sessions", "uploads", "metrics", "gateway", "tax", "refunds"]
CONSTS = ["SCALE", "OFFSET", "LIMIT", "RATE", "BATCH", "RETRIES", "WINDOW", "FACTOR", "MARGIN", "DEPTH"]
BASE_STATEMENT = "Work in the repository at /repo. "


# ================================================================== repofix
class RepoFixEnv(Env):
    family = "repofix"

    def __init__(self, task_id: str, seed: int = 0, n_subtasks: int = 3) -> None:
        super().__init__(task_id, seed, n_subtasks)
        r = self.rng
        mods = r.sample(MODULES, min(len(MODULES), max(n_subtasks + 1, r.randint(4, 7))))
        self.tests: dict[str, list[str]] = {}
        self.consts: dict[str, dict[str, int]] = {}
        for m in mods:
            consts = r.sample(CONSTS, 4)
            vals = {c: r.randint(2, 40) for c in consts}
            self.consts[m] = vals
            lines = [f'"""{m} service helpers."""', "import math", ""]
            for c, v in vals.items():
                lines.append(f"{c} = {v}")
            lines.append("")
            for k in range(3):
                c1, c2 = r.sample(consts, 2)
                lines += [f"def {m}_op{k}(x):", f"    y = x * {c1} + {c2}", f"    return math.floor(y / max(1, {c1}))",
                          ""]
            self.files[f"src/{m}.py"] = "\n".join(lines) + "\n"
            self.tests[m] = [f"test_{m}_{w}" for w in r.sample(WORDS, r.randint(7, 11))]
            self.files[f"tests/test_{m}.py"] = "\n".join(f"def {t}():\n    assert True\n" for t in self.tests[m])
        for j, m in enumerate(r.sample(mods, n_subtasks)):
            key = r.choice(list(self.consts[m]))
            good = self.consts[m][key]
            bad = good + r.choice([-3, -2, -1, 1, 2, 3])
            self.files[f"src/{m}.py"] = self.files[f"src/{m}.py"].replace(f"{key} = {good}\n", f"{key} = {bad}\n")
            test = f"test_{key.lower()}"
            self.tests[m].insert(r.randint(0, len(self.tests[m])), test)
            self.subtasks.append(Subtask(f"s{j + 1}", f"Fix the failing test tests/test_{m}.py::{test}", m,
                                         {"module": m, "key": key, "good": good, "test": test}))
        self.subtasks.sort(key=lambda s: s.id)

    def statement(self) -> str:
        items = "\n".join(f"{i + 1}. {s.goal}" for i, s in enumerate(self.subtasks))
        return (BASE_STATEMENT + "The test suite has regressions. Make these tests pass without breaking others:\n"
                + items + "\nRun `pytest` to see failures. When everything passes, reply with a short summary.")

    def _value(self, m: str, key: str) -> Optional[int]:
        mm = re.search(rf"^{key} = (-?\d+)$", self.files.get(f"src/{m}.py", ""), re.M)
        return int(mm.group(1)) if mm else None

    def _line(self, m: str, key: str) -> int:
        for k, ln in enumerate(self.files[f"src/{m}.py"].split("\n")):
            if ln.startswith(f"{key} = "):
                return k + 1
        return 1

    def failing(self, module: Optional[str] = None) -> list[Subtask]:
        return [s for s in self.subtasks if (module is None or s.key == module) and not self.subtask_ok(s)]

    def subtask_ok(self, st: Subtask) -> bool:
        return self._value(st.key, st.data["key"]) == st.data["good"]

    def verify(self) -> float:
        return sum(self.subtask_ok(s) for s in self.subtasks) / len(self.subtasks)

    def _msg(self, s: Subtask) -> str:
        m, k = s.key, s.data["key"]
        return f"used {k}={self._value(m, k)}, expected {k}={s.data['good']}"

    def family_command(self, argv, rt=None):
        if argv[0] == "python" and argv[1:3] == ["-m", "pytest"]:
            argv = ["pytest"] + argv[3:]
        if argv[0] != "pytest":
            return super().family_command(argv, rt)
        quiet = "-q" in argv
        targets = [a for a in argv[1:] if not a.startswith("-") and a != "line"]
        mods = list(self.tests)
        if targets:
            mods = [m for m in mods if any(t.startswith(f"tests/test_{m}.py") for t in targets)]
            if not mods:
                return f"ERROR: file or directory not found: {targets[0]}\n\nno tests ran in 0.01s", 4
        fails = [s for s in self.failing() if s.key in mods]
        n_total = sum(len(self.tests[m]) for m in mods)
        r = random.Random(_h(self.task_id, "pytest", self.n_bash))
        if quiet:
            out = ["".join("F" if any(f.data["test"] == t and f.key == m for f in fails) else "."
                           for m in mods for t in self.tests[m]) + "  [100%]"]
            out += [f"/repo/src/{s.key}.py:{self._line(s.key, s.data['key'])}: AssertionError: {self._msg(s)}"
                    for s in fails]
            out.append(f"{len(fails)} failed, {n_total - len(fails)} passed in {r.uniform(0.5, 2):.2f}s" if fails
                       else f"{n_total} passed in {r.uniform(0.5, 2):.2f}s")
            return "\n".join(out), 1 if fails else 0
        out = ["=" * 29 + " test session starts " + "=" * 30,
               "platform linux -- Python 3.11.4, pytest-8.2.0, pluggy-1.5.0", "rootdir: /repo",
               "configfile: pyproject.toml", "plugins: cov-5.0.0, xdist-3.6.1", f"collected {n_total} items", ""]
        for m in mods:
            marks = "".join("F" if any(f.data["test"] == t and f.key == m for f in fails) else "." for t in self.tests[m])
            out.append(f"tests/test_{m}.py {marks}")
        if fails:
            out += ["", "=" * 35 + " FAILURES " + "=" * 35]
            for s in fails:
                ln = self._line(s.key, s.data["key"])
                out += [f"{'_' * 30} {s.data['test']} {'_' * 30}", "", f"    def {s.data['test']}():",
                        f'        data = load_fixture("{s.data["key"].lower()}")',
                        f"    >   assert {s.key}.{s.key}_op0(data) == expected(data)",
                        f"E       AssertionError: {self._msg(s)}", "", f"src/{s.key}.py:{ln}: AssertionError",
                        "-" * 30 + " Captured log call " + "-" * 31]
                for k in range(r.randint(90, 150)):
                    out.append(f"DEBUG    {s.key}:{s.key}.py:{r.randint(10, 60)} step {k} batch={r.randint(1, 64)} "
                               f"value={r.randint(0, 99999)} cache_hit={r.choice(['true', 'false'])} "
                               f"elapsed_ms={r.uniform(0, 9):.3f}")
        out += ["", "=" * 31 + " warnings summary " + "=" * 31]
        for k in range(r.randint(12, 24)):
            m = r.choice(mods)
            out += [f"tests/test_{m}.py::{r.choice(self.tests[m])}",
                    f"  /usr/lib/python3.11/site-packages/legacy/compat.py:{r.randint(10, 400)}: DeprecationWarning: "
                    f"'{r.choice(WORDS)}' is deprecated and will be removed in 3.0"]
        out.append("-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html")
        if fails:
            out.append("=" * 27 + " short test summary info " + "=" * 28)
            for s in fails:
                out.append(f"FAILED tests/test_{s.key}.py::{s.data['test']} - src/{s.key}.py:"
                           f"{self._line(s.key, s.data['key'])} {self._msg(s)}")
        tail = (f"{len(fails)} failed, " if fails else "") + f"{n_total - len(fails)} passed, 18 warnings in " \
            f"{r.uniform(1, 4):.2f}s"
        out.append("=" * 20 + f" {tail} " + "=" * 20)
        return "\n".join(out), 1 if fails else 0


# ================================================================== buildfix
IDENTS = ["buf_len", "max_conn", "hdr_size", "retry_cnt", "sock_fd", "msg_id", "tx_bytes", "rx_bytes", "cfg_path",
          "log_lvl", "idle_ms", "peer_cnt"]


def _misspell(ident: str) -> str:
    return ident.replace("_", "", 1) if "_" in ident else ident[:-1]


class BuildFixEnv(Env):
    family = "buildfix"

    def __init__(self, task_id: str, seed: int = 0, n_subtasks: int = 2) -> None:
        super().__init__(task_id, seed, n_subtasks)
        r = self.rng
        pool = ["main", "net", "buf", "parse", "log", "util", "cfg", "io", "tls", "http", "db", "rpc", "sched", "mem",
                "fs", "crc", "zip", "ipc"]
        names = r.sample(pool, min(len(pool), max(r.randint(6, 10), n_subtasks + 2)))
        self.order = sorted(names)
        for f in self.order:
            idents = r.sample(IDENTS, 4)
            lines = [f"/* {f}.c */", '#include "common.h"', ""]
            for i in idents:
                lines.append(f"static int {i} = {r.randint(1, 512)};")
            lines.append("")
            for k in range(r.randint(8, 14)):
                i1, i2 = r.sample(idents, 2)
                lines += [f"int {f}_fn{k}(int x) {{", f"    int tmp{k} = x + {i1};", f"    return tmp{k} * {i2};", "}",
                          ""]
            self.files[f"src/{f}.c"] = "\n".join(lines) + "\n"
        self.warn = {f: r.randint(12, 26) for f in self.order}
        buggy = r.sample(self.order[1:], n_subtasks)
        for j, f in enumerate(sorted(buggy)):
            text = self.files[f"src/{f}.c"]
            ls = text.split("\n")
            cands = [k for k, ln in enumerate(ls) if ln.strip().startswith("int tmp")]
            k = r.choice(cands)
            ident = next(i for i in IDENTS if i in ls[k])
            bad = _misspell(ident)
            ls[k] = ls[k].replace(ident, bad, 1)
            self.files[f"src/{f}.c"] = "\n".join(ls)
            self.subtasks.append(Subtask(f"s{j + 1}", f"Fix the compile error in src/{f}.c", f"src/{f}.c",
                                         {"file": f"src/{f}.c", "bad": bad, "good": ident, "line": k + 1}))

    def statement(self) -> str:
        items = "\n".join(f"{i + 1}. {s.goal}" for i, s in enumerate(self.subtasks))
        return (BASE_STATEMENT + "The C project no longer builds. Fix these compile errors:\n" + items +
                "\nRun `make` to build (it stops at the first failing file). When the build succeeds, reply with a "
                "short summary.")

    def _declared(self, f: str) -> set[str]:
        return set(re.findall(r"^static int (\w+) =", self.files.get(f"src/{f}.c", ""), re.M))

    def _broken(self, f: str) -> Optional[tuple[int, str, str]]:
        """First use of an undeclared identifier: (line, bad, closest declared)."""
        import difflib
        decl = self._declared(f)
        for k, ln in enumerate(self.files.get(f"src/{f}.c", "").split("\n")):
            m = re.match(r"\s*int tmp\d+ = x \+ (\w+);", ln)
            if m and m.group(1) not in decl:
                bad = m.group(1)
                close = difflib.get_close_matches(bad, sorted(decl), n=1, cutoff=0.0)
                return k + 1, bad, close[0] if close else "x"
        return None

    def subtask_ok(self, st: Subtask) -> bool:
        f = st.data["file"][4:-2]
        return self._broken(f) is None and f"x + {st.data['good']};" in self.files.get(st.data["file"], "")

    def verify(self) -> float:
        return sum(self.subtask_ok(s) for s in self.subtasks) / len(self.subtasks)

    def _compile(self, f: str, r: random.Random) -> tuple[list[str], bool]:
        out = [f"cc -O2 -Wall -Wextra -c src/{f}.c -o build/{f}.o"]
        for k in range(self.warn[f]):
            ln = r.randint(5, 60)
            v = f"tmp{r.randint(0, 13)}"
            out += [f"src/{f}.c:{ln}:9: warning: unused variable '{v}' [-Wunused-variable]",
                    f"   {ln} |     int {v} = 0;", "      |         ^~~~"]
        br = self._broken(f)
        if br is None:
            return out, True
        ln, bad, good = br
        out += [f"src/{f}.c: In function '{f}_fn{r.randint(0, 7)}':",
                f"src/{f}.c:{ln}:20: error: '{bad}' undeclared (first use in this function); did you mean '{good}'?",
                f"   {ln} |     int tmpX = x + {bad};", "      |                    ^" + "~" * len(bad),
                f"      |                    {good}",
                f"src/{f}.c:{ln}:20: note: each undeclared identifier is reported only once for each function it "
                f"appears in"]
        for k in range(r.randint(18, 32)):
            l2 = ln + k + 1
            kind = r.random()
            if kind < 0.4:
                out += [f"src/{f}.c:{l2}:12: error: invalid operands to binary * (have 'int *' and 'double')",
                        f"   {l2} |     return tmp{k} * y;", "      |            ^"]
            elif kind < 0.7:
                out += [f"src/{f}.c:{l2}:5: error: expected ';' before 'return'", f"   {l2} |     return 0"]
            else:
                out += [f"src/{f}.c:{l2}:9: warning: implicit declaration of function '{f}_flush' "
                        f"[-Wimplicit-function-declaration]"]
        out += [f"make: *** [Makefile:14: build/{f}.o] Error 1"]
        return out, False

    def family_command(self, argv, rt=None):
        if argv[0] != "make":
            return super().family_command(argv, rt)
        r = random.Random(_h(self.task_id, "make", self.n_bash))
        target = next((a for a in argv[1:] if not a.startswith("-")), None)
        if target and target.startswith("build/") and target.endswith(".o"):
            f = target[6:-2]
            if f not in self.order:
                return f"make: *** No rule to make target '{target}'.  Stop.", 2
            out, ok = self._compile(f, r)
            return "\n".join(out), 0 if ok else 2
        out = []
        for f in self.order:
            lines, ok = self._compile(f, r)
            out += lines
            if not ok:
                return "\n".join(out), 2
        out += ["cc -o build/app build/*.o -lm", "Build succeeded."]
        return "\n".join(out), 0


# ================================================================== logtriage
COMPONENTS = ["db", "api", "cache", "auth", "queue", "search", "billing", "mailer"]


class LogTriageEnv(Env):
    family = "logtriage"
    style = "batch"

    def __init__(self, task_id: str, seed: int = 0, n_subtasks: int = 3) -> None:
        super().__init__(task_id, seed, n_subtasks)
        r = self.rng
        comps = r.sample(COMPONENTS, 5)
        n = r.randint(380, 470)
        lines = []
        t = r.randint(0, 3000)
        for k in range(n):
            t += r.randint(1, 9)
            lvl = r.choices(["INFO", "DEBUG", "WARN", "ERROR", "FATAL"], [50, 25, 12, 11, 1.2])[0]
            c = r.choice(comps)
            msg = r.choice(["request completed", "cache miss for key", "retrying upstream call", "connection reset",
                            "timeout waiting for lock", "slow query detected", "user session refreshed",
                            "payload validated", "timeout talking to peer", "queue depth high"])
            lines.append(f"2026-08-21T{10 + t // 3600:02d}:{t // 60 % 60:02d}:{t % 60:02d}Z {lvl:5s} [{c}] {msg} "
                         f"req={r.randint(100000, 999999)} dur={r.randint(1, 4000)}ms")
        if not any(" FATAL " in l for l in lines):
            i = r.randint(n // 3, n - 1)
            lines[i] = lines[i].replace(lines[i].split()[1], "FATAL", 1)
        self.files["logs/app.log"] = "\n".join(lines) + "\n"
        pool = [("first_fatal", None)] + [(k, c) for k in ("errors", "timeouts", "warns") for c in comps]
        items = [pool[0]] + r.sample(pool[1:], n_subtasks - 1) if r.random() < 0.5 else r.sample(pool[1:], n_subtasks)
        r.shuffle(items)
        for j, (kind, c) in enumerate(items):
            name = kind if kind == "first_fatal" else f"{kind}[{c}]"
            desc = {"errors": f"number of ERROR lines from component {c}",
                    "timeouts": f"number of lines from component {c} whose message mentions a timeout",
                    "first_fatal": "timestamp of the first FATAL line",
                    "warns": f"number of WARN lines from component {c}"}[kind]
            self.subtasks.append(Subtask(f"s{j + 1}", f"{name}=<{desc}>", name,
                                         {"kind": kind, "component": c, "answer": self.compute(kind, c, lines)}))

    @staticmethod
    def compute(kind: str, c: str, lines: list[str]) -> str:
        if kind == "first_fatal":
            return next((l.split()[0] for l in lines if " FATAL " in l), "none")
        lvl = {"errors": "ERROR", "warns": "WARN"}.get(kind)
        if lvl:
            return str(sum(1 for l in lines if re.search(rf"\s{lvl}\s+\[{c}\]", l)))
        return str(sum(1 for l in lines if f"[{c}]" in l and "timeout" in l))

    def statement(self) -> str:
        items = "\n".join(f"{i + 1}. {s.goal}" for i, s in enumerate(self.subtasks))
        return (BASE_STATEMENT + "Analyse logs/app.log and write report.txt containing exactly these lines "
                "(key=value):\n" + items + "\nThen run `make check` to validate the report. When the check passes, "
                "reply with a short summary.")

    def _reported(self) -> dict[str, str]:
        out = {}
        for ln in self.files.get("report.txt", "").splitlines():
            if "=" in ln:
                k, v = ln.split("=", 1)
                out[k.strip()] = v.strip()
        return out

    def subtask_ok(self, st: Subtask) -> bool:
        return self._reported().get(st.key) == st.data["answer"]

    def verify(self) -> float:
        return sum(self.subtask_ok(s) for s in self.subtasks) / len(self.subtasks)

    def family_command(self, argv, rt=None):
        if argv[0] != "make" or "check" not in argv:
            return super().family_command(argv, rt)
        r = random.Random(_h(self.task_id, "check", self.n_bash))
        out = ["python3 tools/check_report.py --report report.txt --log logs/app.log --rules tools/rules.yaml"]
        for k in range(r.randint(50, 80)):
            out.append(f"[check] {r.choice(['loading', 'compiling', 'indexing', 'validating'])} rule "
                       f"{r.randint(1, 400):03d} ({r.choice(COMPONENTS)}) ... ok  t={r.uniform(0, 2):.3f}s")
        rep = self._reported()
        fails = 0
        if "report.txt" not in self.files:
            out.append("FAILED: report.txt not found")
            fails = len(self.subtasks)
        else:
            for s in self.subtasks:
                if s.key not in rep:
                    out.append(f"FAILED check {s.key}: missing from report")
                    fails += 1
                elif rep[s.key] != s.data["answer"]:
                    out.append(f"FAILED check {s.key}: reported {rep[s.key]} does not match the log")
                    fails += 1
                else:
                    out.append(f"check {s.key} ... ok")
        out.append(f"{len(self.subtasks) - fails}/{len(self.subtasks)} report checks passed")
        if fails:
            out.append("make: *** [Makefile:9: check] Error 1")
        return "\n".join(out), 2 if fails else 0


# ================================================================== datalookup (held-out)
STATUSES = ["shipped", "pending", "cancelled", "returned", "delivered"]
REGIONS = ["eu", "us", "apac", "latam"]


class DataLookupEnv(Env):
    family = "datalookup"
    style = "batch"

    def __init__(self, task_id: str, seed: int = 0, n_subtasks: int = 3) -> None:
        super().__init__(task_id, seed, n_subtasks)
        r = self.rng
        n = r.randint(300, 380)
        self.records = []
        ids = r.sample(range(1000, 9999), n)
        for k in range(n):
            self.records.append({"id": f"ORD-{ids[k]}", "customer": f"C-{r.randint(1, 60)}",
                                 "status": r.choice(STATUSES), "qty": r.randint(1, 20), "region": r.choice(REGIONS),
                                 "note": r.choice(["gift", "bulk", "expedite", "standard", "fragile"])})
        self.files["data/records.jsonl"] = "\n".join(json.dumps(x) for x in self.records) + "\n"
        kinds = ["status", "qty_sum", "region_count", "status"]
        for j in range(n_subtasks):
            kind = kinds[j % len(kinds)]
            if kind == "status":
                rec = r.choice(self.records)
                q, key, ans = f"What is the status of order {rec['id']}?", rec["id"], rec["status"]
            elif kind == "qty_sum":
                cust = r.choice(self.records)["customer"]
                q, key = f"What is the total qty over all orders of customer {cust}?", cust
                ans = str(sum(x["qty"] for x in self.records if x["customer"] == cust))
            else:
                reg, st = r.choice(REGIONS), r.choice(STATUSES)
                q, key = f"How many orders in region {reg} have status {st}?", f"{reg}/{st}"
                ans = str(sum(1 for x in self.records if x["region"] == reg and x["status"] == st))
            self.subtasks.append(Subtask(f"s{j + 1}", q, key, {"kind": kind, "answer": ans, "line": j + 1}))

    def statement(self) -> str:
        items = "\n".join(f"{i + 1}. {s.goal}" for i, s in enumerate(self.subtasks))
        return (BASE_STATEMENT + "Answer these questions about data/records.jsonl. Write answers.txt with one line "
                "per question in the form '<n>: <answer>':\n" + items + "\nThen run `pytest tests/test_answers.py` to "
                "check the answers. When it passes, reply with a short summary.")

    def _answers(self) -> dict[int, str]:
        out = {}
        for ln in self.files.get("answers.txt", "").splitlines():
            m = re.match(r"\s*(\d+)\s*:\s*(.*)$", ln)
            if m:
                out[int(m.group(1))] = m.group(2).strip()
        return out

    def subtask_ok(self, st: Subtask) -> bool:
        return self._answers().get(st.data["line"]) == st.data["answer"]

    def verify(self) -> float:
        return sum(self.subtask_ok(s) for s in self.subtasks) / len(self.subtasks)

    def family_command(self, argv, rt=None):
        if argv[0] == "python" and argv[1:3] == ["-m", "pytest"]:
            argv = ["pytest"] + argv[3:]
        if argv[0] != "pytest":
            return super().family_command(argv, rt)
        r = random.Random(_h(self.task_id, "pytest", self.n_bash))
        bad = [s for s in self.subtasks if not self.subtask_ok(s)]
        quiet = "-q" in argv
        if not bad:
            return ("1 passed in 0.31s" if quiet else "=" * 29 + " test session starts " + "=" * 30 +
                    "\ncollected 1 item\n\ntests/test_answers.py .  [100%]\n\n" + "=" * 30 + " 1 passed in 0.31s " +
                    "=" * 30), 0
        if quiet:
            return "F  [100%]\n/repo/tests/test_answers.py:9: AssertionError\n1 failed in 0.33s", 1
        msg = "; ".join(f"answers.txt line {s.data['line']} is wrong" for s in bad)
        out = ["=" * 29 + " test session starts " + "=" * 30, "platform linux -- Python 3.11.4, pytest-8.2.0",
               "collected 1 item", "", "tests/test_answers.py F  [100%]", "", "=" * 35 + " FAILURES " + "=" * 35,
               "_" * 33 + " test_answers " + "_" * 33, f"E   AssertionError: {msg}",
               "E   (the checker's view of the data follows)", "-" * 29 + " Captured stdout call " + "-" * 29]
        for rec in r.sample(self.records, min(len(self.records), r.randint(120, 200))):
            out.append("loaded " + json.dumps(rec))
        out += ["=" * 27 + " short test summary info " + "=" * 28,
                "FAILED tests/test_answers.py::test_answers - AssertionError", "=" * 30 + " 1 failed in 0.41s " +
                "=" * 30]
        return "\n".join(out), 1


# ================================================================== configfix (held-out)
SERVICES = ["billing", "gateway", "search", "ledger", "notify", "catalog", "orders", "profile", "metrics", "sessions",
            "uploads", "pricing", "reports", "queue", "auth", "cdn", "tax", "refunds", "inventory", "mailer"]
CFG_KEYS = {"replicas": (1, 12), "port": (1024, 65535), "timeout_ms": (50, 30000), "workers": (1, 64),
            "cache_mb": (16, 4096), "retries": (0, 10)}


class ConfigFixEnv(Env):
    """Held-out family: a validator that reports the FIRST invalid setting at the top of a long
    resolved-configuration dump (other format, other command, evidence at the head)."""

    family = "configfix"

    def __init__(self, task_id: str, seed: int = 0, n_subtasks: int = 4) -> None:
        super().__init__(task_id, seed, n_subtasks)
        r = self.rng
        self.services = r.sample(SERVICES, min(len(SERVICES), max(r.randint(10, 16), n_subtasks + 2)))
        self.values: dict[tuple[str, str], int] = {}
        for sv in self.services:
            for k, (lo, hi) in CFG_KEYS.items():
                self.values[(sv, k)] = r.randint(lo, hi)
        self.bad = r.sample(sorted(self.values), n_subtasks)
        lines = ["# service configuration", "services:"]
        for sv in self.services:
            lines.append(f"  {sv}:")
            for k in CFG_KEYS:
                v = self.values[(sv, k)]
                if (sv, k) in self.bad:
                    lo, hi = CFG_KEYS[k]
                    v = r.choice([lo - r.randint(1, 50), hi + r.randint(1, 5000)])
                lines.append(f"    {k}: {v}  # {sv}")
        self.files["config/services.yaml"] = "\n".join(lines) + "\n"
        order = list(self.bad)
        r.shuffle(order)
        for j, (sv, k) in enumerate(order):
            self.subtasks.append(Subtask(f"s{j + 1}", f"Fix the invalid setting services.{sv}.{k}", f"{sv}.{k}",
                                         {"svc": sv, "key": k, "good": self.values[(sv, k)]}))

    def statement(self) -> str:
        items = "\n".join(f"{i + 1}. {s.goal}" for i, s in enumerate(self.subtasks))
        return (BASE_STATEMENT + "config/services.yaml fails validation. Fix these invalid settings:\n" + items +
                "\nRun `make validate` to check (it stops at the first error and reports the value it expects). "
                "When validation passes, reply with a short summary.")

    def _current(self) -> dict[tuple[str, str], tuple[int, int]]:
        out = {}
        for k, ln in enumerate(self.files.get("config/services.yaml", "").split("\n")):
            m = re.match(r"\s+(\w+): (-?\d+)  # (\w+)$", ln)
            if m:
                out[(m.group(3), m.group(1))] = (int(m.group(2)), k + 1)
        return out

    def subtask_ok(self, st: Subtask) -> bool:
        return self._current().get((st.data["svc"], st.data["key"]), (None, 0))[0] == st.data["good"]

    def verify(self) -> float:
        return sum(self.subtask_ok(s) for s in self.subtasks) / len(self.subtasks)

    def family_command(self, argv, rt=None):
        if argv[0] != "make" or "validate" not in argv:
            return super().family_command(argv, rt)
        r = random.Random(_h(self.task_id, "validate", self.n_bash))
        cur = self._current()
        out = ["python3 tools/validate.py --schema schema/services.json config/services.yaml"]
        err = None
        for sv in self.services:
            for k, (lo, hi) in CFG_KEYS.items():
                v, ln = cur.get((sv, k), (None, 0))
                if v is None:
                    continue
                if not (lo <= v <= hi) or ((sv, k) in self.bad and v != self.values[(sv, k)]):
                    err = (sv, k, v, ln)
                    break
            if err:
                break
        if err:
            sv, k, v, ln = err
            out += [f"ERROR config/services.yaml:{ln}: services.{sv}.{k}: got {v}, expected {self.values[(sv, k)]}",
                    "validation stopped after the first error", ""]
        else:
            out += ["config OK: all settings valid", ""]
        out.append("Effective configuration (resolved, including defaults):")
        for sv in self.services:
            for k in CFG_KEYS:
                v, ln = cur.get((sv, k), (None, 0))
                out.append(f"services.{sv}.{k} = {v}  [source: config/services.yaml:{ln}]")
            for d in range(r.randint(10, 16)):
                out.append(f"services.{sv}.opt_{d:02d} = {r.randint(0, 999)}  [source: default]")
        if err:
            out.append("make: *** [Makefile:21: validate] Error 1")
        return "\n".join(out), 2 if err else 0


FAMILIES = {"repofix": RepoFixEnv, "buildfix": BuildFixEnv, "logtriage": LogTriageEnv, "datalookup": DataLookupEnv,
            "configfix": ConfigFixEnv}
