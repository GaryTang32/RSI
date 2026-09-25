"""Code-style AgentWorld families: repofix, buildfix (training) and configfix (held-out)."""
from __future__ import annotations

import json
import random
import re
from typing import Optional

from .base import BASE_STATEMENT, CONSTS, MODULES, WORDS, Env, Subtask, _h


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

    def apply_reference(self) -> None:
        for st in self.subtasks:
            m, k = st.key, st.data["key"]
            self.files[f"src/{m}.py"] = re.sub(rf"^{k} = -?\d+$", f"{k} = {st.data['good']}",
                                               self.files[f"src/{m}.py"], flags=re.M)

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

    def apply_reference(self) -> None:
        for st in self.subtasks:
            p = st.data["file"]
            self.files[p] = self.files[p].replace(f"x + {st.data['bad']};", f"x + {st.data['good']};")

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

    def apply_reference(self) -> None:
        text = self.files["config/services.yaml"]
        for st in self.subtasks:
            sv, k = st.data["svc"], st.data["key"]
            text = re.sub(rf"^(\s+{k}: )-?\d+(  # {sv})$", rf"\g<1>{st.data['good']}\2", text, flags=re.M)
        self.files["config/services.yaml"] = text

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


