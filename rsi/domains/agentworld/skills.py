"""What the simulated agent "knows" about each family's tools and output formats.

Each skill parses evidence ONLY from text the agent can see (tool results, receipts, placeholder
excerpts, recall pages, its own NOTE lines) and proposes the corresponding fix / check commands.
"""
from __future__ import annotations

import json
import re

# ------------------------------------------------------------------ family skills (what the model knows)
class Skill:
    style = "per_subtask"

    def parse(self, statement: str) -> list[dict]:
        raise NotImplementedError


class RepoFixSkill(Skill):
    def parse(self, statement):
        return [{"id": f"s{i + 1}", "key": m.group(1), "test": m.group(2), "goal": m.group(0)}
                for i, m in enumerate(re.finditer(r"Fix the failing test tests/test_(\w+)\.py::(\w+)", statement))]

    def diag(self, st, first, smart):
        if first:
            return "pytest"
        return f"pytest -q --tb=line tests/test_{st['key']}.py" if smart else f"pytest tests/test_{st['key']}.py"

    def find(self, st, text):
        m = st["key"]
        r = re.search(rf"src/{m}\.py:(\d+):? (?:AssertionError: )?used (\w+)=(-?\d+), expected \2=(-?\d+)", text)
        if r:
            return {"key": r.group(2), "bad": r.group(3), "good": r.group(4)}
        r = re.search(rf"AssertionError: used (\w+)=(-?\d+), expected \1=(-?\d+)\n(?:[^\n]*\n){{0,3}}?src/{m}\.py:\d+: "
                      rf"AssertionError", text)
        if r:
            return {"key": r.group(1), "bad": r.group(2), "good": r.group(3)}
        r = re.search(rf"NOTE: evidence {m}: (\w+) (-?\d+)->(-?\d+)", text)
        if r:
            return {"key": r.group(1), "bad": r.group(2), "good": r.group(3)}
        return None

    def note(self, st, ev):
        return f"NOTE: evidence {st['key']}: {ev['key']} {ev['bad']}->{ev['good']}"

    def pattern(self, st):
        return f"src/{st['key']}.py"

    def fix(self, st, ev, wrong):
        g = int(ev["good"])
        good = str(g + (1 if g + 1 != int(ev["bad"]) else 2)) if wrong else ev["good"]
        return "edit", {"path": f"src/{st['key']}.py", "old": f"{ev['key']} = {ev['bad']}", "new": f"{ev['key']} = {good}"}

    def file(self, st):
        return f"src/{st['key']}.py"

    def verify_cmd(self, st):
        return f"pytest tests/test_{st['key']}.py"

    def verdict(self, st, text):
        if self.find(st, text) and "NOTE:" not in text:
            return False
        if "status=failure" in text:
            return False
        if re.search(r"\b\d+ failed", text):
            return False
        if re.search(r"\b\d+ passed", text) or "status=success" in text:
            return True
        return None

    def full_cmd(self):
        return "pytest"


class BuildFixSkill(RepoFixSkill):
    def parse(self, statement):
        return [{"id": f"s{i + 1}", "key": m.group(1), "goal": m.group(0)}
                for i, m in enumerate(re.finditer(r"Fix the compile error in src/(\w+)\.c", statement))]

    def diag(self, st, first, smart):
        return "make 2>&1 | grep -m 5 error" if (smart and not first) else "make"

    def find(self, st, text):
        f = st["key"]
        r = re.search(rf"src/{f}\.c:(\d+):\d+: error: '(\w+)' undeclared[^\n]*did you mean '(\w+)'", text)
        if r:
            return {"bad": r.group(2), "good": r.group(3), "line": r.group(1)}
        r = re.search(rf"NOTE: evidence {f}: (\w+)->(\w+)", text)
        if r:
            return {"bad": r.group(1), "good": r.group(2)}
        return None

    def note(self, st, ev):
        return f"NOTE: evidence {st['key']}: {ev['bad']}->{ev['good']}"

    def pattern(self, st):
        return f"src/{st['key']}.c"

    def fix(self, st, ev, wrong):
        good = ev["good"][:-1] if wrong else ev["good"]
        return "edit", {"path": f"src/{st['key']}.c", "old": f"x + {ev['bad']};", "new": f"x + {good};"}

    def file(self, st):
        return f"src/{st['key']}.c"

    def verify_cmd(self, st):
        return f"make build/{st['key']}.o"

    def verdict(self, st, text):
        if re.search(rf"src/{st['key']}\.c:\d+:\d+: error", text) or "status=failure" in text:
            return False
        if "Error 1" in text or "Error 2" in text:
            return False
        if f"build/{st['key']}.o" in text or "Build succeeded" in text or "status=success" in text:
            return True
        return None

    def full_cmd(self):
        return "make"


class ConfigFixSkill(RepoFixSkill):
    def parse(self, statement):
        return [{"id": f"s{i + 1}", "key": f"{m.group(1)}.{m.group(2)}", "svc": m.group(1), "k": m.group(2),
                 "goal": m.group(0)}
                for i, m in enumerate(re.finditer(r"Fix the invalid setting services\.(\w+)\.(\w+)", statement))]

    def diag(self, st, first, smart):
        return "make validate | head -n 5" if (smart and not first) else "make validate"

    def find(self, st, text):
        r = re.search(rf"services\.{st['svc']}\.{st['k']}: got (-?\d+), expected (-?\d+)", text)
        if r:
            return {"bad": r.group(1), "good": r.group(2)}
        r = re.search(rf"NOTE: evidence {re.escape(st['key'])}: (-?\d+)->(-?\d+)", text)
        if r:
            return {"bad": r.group(1), "good": r.group(2)}
        return None

    def note(self, st, ev):
        return f"NOTE: evidence {st['key']}: {ev['bad']}->{ev['good']}"

    def pattern(self, st):
        return f"services.{st['svc']}.{st['k']}"

    def fix(self, st, ev, wrong):
        good = str(int(ev["good"]) + 1) if wrong else ev["good"]
        return "edit", {"path": "config/services.yaml", "old": f"{st['k']}: {ev['bad']}  # {st['svc']}",
                        "new": f"{st['k']}: {good}  # {st['svc']}"}

    def file(self, st):
        return "config/services.yaml"

    def verify_cmd(self, st):
        return "make validate"

    def verdict(self, st, text):
        if self.find(st, text) and "NOTE:" not in text:
            return False
        if "config OK" in text or re.search(r"ERROR config/services\.yaml:\d+: services\.", text) or \
                "status=success" in text:
            return True
        if "status=failure" in text:
            return None
        return None

    def full_cmd(self):
        return "make validate"


class LogTriageSkill(Skill):
    style = "batch"
    read_cmd = "cat logs/app.log"
    check_cmd = "make check"
    out_file = "report.txt"

    def parse(self, statement):
        out = []
        for i, m in enumerate(re.finditer(r"^\d+\. ((\w+)(?:\[(\w+)\])?)=<", statement, re.M)):
            out.append({"id": f"s{i + 1}", "key": m.group(1), "kind": m.group(2), "comp": m.group(3), "n": i + 1})
        return out

    def is_data(self, text):
        return len(re.findall(r"^\d{4}-\d\d-\d\dT[\d:]+Z ", text, re.M)) >= 5

    def compute(self, st, text):
        lines = [l for l in text.split("\n") if re.match(r"^\d{4}-\d\d-\d\dT", l)]
        if not lines:
            return None
        k, c = st["kind"], st["comp"]
        if k == "first_fatal":
            v = next((l.split()[0] for l in lines if " FATAL " in l), None)
            return v
        lvl = {"errors": "ERROR", "warns": "WARN"}.get(k)
        if lvl:
            return str(sum(1 for l in lines if re.search(rf"\s{lvl}\s+\[{c}\]", l)))
        return str(sum(1 for l in lines if f"[{c}]" in l and "timeout" in l))

    def smart(self, st):
        k, c = st["kind"], st["comp"]
        if k == "first_fatal":
            return "grep -m 1 ' FATAL ' logs/app.log"
        if k == "timeouts":
            return f"grep -c '\\[{c}\\].*timeout' logs/app.log"
        lvl = {"errors": "ERROR", "warns": "WARN"}[k]
        return f"grep -c '{lvl}\\s+\\[{c}\\]' logs/app.log"

    def parse_smart(self, st, text):
        text = text.strip().split("\n")[0].strip() if text.strip() else ""
        if st["kind"] == "first_fatal":
            m = re.match(r"(\d{4}-\d\d-\d\dT[\d:]+Z)", text)
            return m.group(1) if m else None
        return text if re.fullmatch(r"\d+", text) else None

    def perturb(self, st, v, rng):
        if re.fullmatch(r"\d+", v or ""):
            return str(max(0, int(v) + rng.choice([-1, 1])))
        return (v or "")[:-1] + "0"

    def render(self, items, values):
        return "\n".join(f"{st['key']}={values.get(st['id'], '')}" for st in items) + "\n"

    def check(self, items, text):
        """-> (passed | None, failed ids | None)"""
        failed = {st["id"] for st in items if re.search(rf"FAILED check {re.escape(st['key'])}:", text)}
        if failed:
            return False, failed
        if re.search(r"(\d+)/\1 report checks passed", text) or "status=success" in text:
            return True, set()
        if "FAILED" in text or "Error" in text or "status=failure" in text:
            return False, None
        return None, None


class DataLookupSkill(LogTriageSkill):
    read_cmd = "cat data/records.jsonl"
    check_cmd = "pytest tests/test_answers.py"
    out_file = "answers.txt"

    def parse(self, statement):
        out = []
        for i, m in enumerate(re.finditer(r"^(\d+)\. (.+\?)$", statement, re.M)):
            q = m.group(2)
            if (r := re.search(r"status of order (ORD-\d+)", q)):
                out.append({"id": f"s{i + 1}", "kind": "status", "key": r.group(1), "n": int(m.group(1))})
            elif (r := re.search(r"qty over all orders of customer (C-\d+)", q)):
                out.append({"id": f"s{i + 1}", "kind": "qty_sum", "key": r.group(1), "n": int(m.group(1))})
            elif (r := re.search(r"region (\w+) have status (\w+)", q)):
                out.append({"id": f"s{i + 1}", "kind": "region_count", "key": f"{r.group(1)}/{r.group(2)}",
                            "n": int(m.group(1))})
        return out

    def is_data(self, text):
        return text.count('{"id": "ORD-') >= 5

    def compute(self, st, text):
        recs = []
        for l in text.split("\n"):
            l = l.strip()
            if l.startswith('{"id": "ORD-'):
                try:
                    recs.append(json.loads(l))
                except json.JSONDecodeError:
                    pass
        if not recs:
            return None
        if st["kind"] == "status":
            r = next((x for x in recs if x["id"] == st["key"]), None)
            return r["status"] if r else None
        if st["kind"] == "qty_sum":
            return str(sum(x["qty"] for x in recs if x["customer"] == st["key"]))
        reg, s = st["key"].split("/")
        return str(sum(1 for x in recs if x["region"] == reg and x["status"] == s))

    def smart(self, st):
        if st["kind"] == "status":
            return f"grep {st['key']} data/records.jsonl"
        if st["kind"] == "qty_sum":
            return f"grep -F '\"customer\": \"{st['key']}\"' data/records.jsonl"
        reg, s = st["key"].split("/")
        return f"grep -c '\"status\": \"{s}\", \"qty\": [0-9]*, \"region\": \"{reg}\"' data/records.jsonl"

    def parse_smart(self, st, text):
        if st["kind"] == "region_count":
            t = text.strip().split("\n")[0].strip() if text.strip() else ""
            return t if re.fullmatch(r"\d+", t) else None
        return self.compute(st, text) if text.strip() else None

    def render(self, items, values):
        return "\n".join(f"{st['n']}: {values.get(st['id'], '')}" for st in items) + "\n"

    def check(self, items, text):
        lines = {int(x) for x in re.findall(r"answers\.txt line (\d+) is wrong", text)}
        if lines:
            return False, {st["id"] for st in items if st["n"] in lines}
        if re.search(r"\b1 passed", text) or "status=success" in text:
            return True, set()
        if "failed" in text or "AssertionError" in text or "status=failure" in text:
            return False, None
        return None, None


SKILLS = {"repofix": RepoFixSkill(), "buildfix": BuildFixSkill(), "logtriage": LogTriageSkill(),
          "datalookup": DataLookupSkill(), "configfix": ConfigFixSkill()}


def skill_for(statement: str) -> Skill:
    if "failing test" in statement:
        return SKILLS["repofix"]
    if "compile error" in statement:
        return SKILLS["buildfix"]
    if "logs/app.log" in statement:
        return SKILLS["logtriage"]
    if "invalid settings" in statement:
        return SKILLS["configfix"]
    return SKILLS["datalookup"]


