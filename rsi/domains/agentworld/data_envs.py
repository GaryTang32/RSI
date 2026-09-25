"""Report-style AgentWorld families: logtriage (training) and datalookup (held-out)."""
from __future__ import annotations

import json
import random
import re
from typing import Optional

from .base import BASE_STATEMENT, Env, Subtask, _h


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

    def apply_reference(self) -> None:
        self.files["report.txt"] = "\n".join(f"{s.key}={s.data['answer']}" for s in self.subtasks) + "\n"

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

    def apply_reference(self) -> None:
        self.files["answers.txt"] = "\n".join(f"{s.data['line']}: {s.data['answer']}" for s in self.subtasks) + "\n"

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


