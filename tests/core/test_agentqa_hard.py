"""The harder AgentQA families: every task has exactly one answer, re-derived here from the question text alone."""
from __future__ import annotations

import datetime as dt
import itertools
import re

import pytest

from rsi.domains.agentqa import AgentQADomain
from rsi.domains.agentqa.hard import OOD_HINT, make_hard_suite


def _tasks(fam: str):
    s = make_hard_suite(n_evolve=12, n_holdout=6, n_ood_per_family=8, seed=3)
    for split in ("holdout", "ood"):
        s.unseal(split)
    return [t for split in ("evolve", "holdout", "ood") for t in s.split(split) if t.family == fam]


ATTR_ORDER = ("name", "color", "pet", "drink", "hobby")


def _solve_logic(q: str) -> list[str]:
    """Independent solver: fills houses left to right (not the generator's per-value CSP) and checks each clue
    as soon as every value it mentions is placed."""
    head = q.split("Clues:")[0]
    names = re.search(r"people are ([^.]+)\.", head).group(1).split(", ")
    lists = re.findall(r"\(([^)]+)\)", head)
    vals = {"name": names, "color": lists[0].split(", "), "pet": lists[1].split(", "), "drink": lists[2].split(", "),
            "hobby": lists[3].split(", ")}
    n = len(names)

    def ref(phrase: str):
        phrase = phrase.strip().rstrip(".")
        for a, pat in (("color", r"the person in the (\w+) house"), ("pet", r"the (\w+) owner"),
                       ("drink", r"the person who drinks (\w+)"), ("hobby", r"the person who does (\w+)")):
            m = re.fullmatch(pat, phrase, re.I)
            if m:
                return a, m.group(1).lower()
        return "name", phrase

    checks = []
    for c in re.findall(r"^\d+\. (.+)$", q, re.M):
        if m := re.fullmatch(r"(.+) lives in house (\d)\.", c):
            checks.append(([ref(m.group(1))], lambda p, h=int(m.group(2)) - 1: p[0] == h))
            continue
        for pat, f in ((r"(.+) lives immediately to the left of (.+)\.", lambda p: p[0] + 1 == p[1]),
                       (r"(.+) lives next to (.+)\.", lambda p: abs(p[0] - p[1]) == 1),
                       (r"(.+) is not (.+)\.", lambda p: p[0] != p[1]), (r"(.+) is (.+)\.", lambda p: p[0] == p[1])):
            if m := re.fullmatch(pat, c):
                checks.append(([ref(m.group(1)), ref(m.group(2))], f))
                break
        else:
            raise AssertionError(c)
    sols, where = [], {}

    def ok():
        for refs, f in checks:
            if all(r in where for r in refs) and not f([where[r] for r in refs]):
                return False
        return True

    def place(h: int):
        if h == n:
            sols.append(dict(where))
            return
        free = [[v for v in vals[a] if (a, v) not in where] for a in ATTR_ORDER]
        for combo in itertools.product(*free):
            for a, v in zip(ATTR_ORDER, combo):
                where[(a, v)] = h
            if ok():
                place(h + 1)
            for a, v in zip(ATTR_ORDER, combo):
                del where[(a, v)]

    place(0)
    m = re.search(r"Who (owns the (\w+)|drinks (\w+)|lives in the (\w+) house|does (\w+))\?", q)
    a, v = next((a, g) for a, g in zip(("pet", "drink", "color", "hobby"), m.groups()[1:]) if g)
    return [next(nm for nm in names if s[("name", nm)] == s[(a, v)]) for s in sols]


def test_logic_puzzles_have_one_solution_matching_the_target():
    ts = _tasks("logic")
    assert len(ts) >= 8
    for t in ts:
        assert _solve_logic(t.input) == [t.target], t.id


def _parse_date(s: str) -> dt.date:
    for f in ("%Y-%m-%d", "%d/%m/%Y", "%b %d %Y"):
        try:
            return dt.datetime.strptime(s, f).date()
        except ValueError:
            pass
    raise AssertionError(s)


def test_ledger_targets_recompute_from_the_table():
    status = {"paid": "paid", "PAID": "paid", "settled": "paid", "refunded": "refunded", "REFUND": "refunded",
              "chargeback": "refunded", "pending": "pending", "on hold": "pending"}
    for t in _tasks("ledger"):
        q = t.input
        rate = float(re.search(r"1 EUR = ([\d.]+) USD", q).group(1))
        last = {}
        for tid, d, reg, cu, amt, st in re.findall(r"^(T\d+) \| ([^|]+) \| (\w+) \| (\w+) \| ([^|]+) \| (.+)$", q, re.M):
            amt = amt.strip()
            usd = (float(amt[:-4].replace(".", "").replace(",", ".")) * rate if amt.endswith(" EUR")
                   else float(amt.lstrip("$").replace(",", "")))
            last[tid] = (_parse_date(d.strip()), reg, cu, usd, status[st.strip()])
        lo, hi = (dt.date.fromisoformat(x) for x in re.search(r"from (\S+) to (\S+) inclusive", q).groups())
        rows = [r for r in last.values() if lo <= r[0] <= hi]
        if "Which customer" in q:
            net = {}
            for _, _, cu, v, st in rows:
                net[cu] = net.get(cu, 0) + {"paid": v, "refunded": -v}.get(st, 0)
            got = max(sorted(net), key=lambda k: net[k])
        elif "average" in q:
            vs = [v for *_, v, st in rows if st == "paid"]
            got = f"{round(sum(vs) / len(vs) + 1e-9, 2):.2f}"
        else:
            reg = re.search(r"in the (\w+) region", q).group(1)
            got = f"{round(sum(v for _, r, _, v, st in rows if r == reg and st == 'paid') + 1e-9, 2):.2f}"
        assert got == t.target, t.id
        assert len(last) < len(re.findall(r"^T\d+ ", q, re.M))              # every ledger has correction rows


def test_schedule_and_cipher_targets_recompute():
    for t in _tasks("schedule"):
        n = int(re.search(r"needs (\d+) business days", t.input).group(1))
        d = dt.date.fromisoformat(re.search(r"starts on (\S+)", t.input).group(1))
        hol = {dt.date.fromisoformat(x) for x in re.findall(r"\d{4}-\d\d-\d\d", t.input.split("holidays")[1])}
        while True:
            if d.weekday() < 5 and d not in hol:
                n -= 1
                if n == 0:
                    break
            d += dt.timedelta(days=1)
        assert d.isoformat() == t.target, t.id
    for t in _tasks("cipher"):
        x = re.search(r"string '(\w+)'", t.input).group(1)
        for step in re.findall(r"^\d+\. (.+)$", t.input, re.M):
            if step.startswith("reverse"):
                x = x[::-1]
            elif m := re.match(r"shift every letter forward in the alphabet by (\d+)", step):
                x = "".join(chr((ord(c) - 97 + int(m.group(1))) % 26 + 97) for c in x)
            elif step.startswith("swap"):
                x = "".join(x[i:i + 2][::-1] for i in range(0, len(x), 2))
            elif m := re.match(r"rotate the string left by (\d+)", step):
                r = int(m.group(1)); x = x[r:] + x[:r]
            elif m := re.match(r"delete every (\d+)(?:rd|th)", step):
                k = int(m.group(1)); x = "".join(c for i, c in enumerate(x) if (i + 1) % k)
            elif step.startswith("replace each vowel"):
                x = x.translate(str.maketrans("aeiou", "eioua"))
            elif step.startswith("split the string"):
                h = (len(x) + 1) // 2
                x = "".join(p + q for p, q in itertools.zip_longest(x[:h], x[h:], fillvalue=""))
            else:
                raise AssertionError(step)
        assert x == t.target, t.id


def test_hard_suite_splits_and_domain_hint():
    s = make_hard_suite(seed=0)
    assert s.families("evolve") == ["ledger", "logic"] or set(s.families("evolve")) == {"ledger", "logic"}
    assert s.is_sealed("holdout") and s.is_sealed("ood")
    dom = AgentQADomain(s, ood_hint=OOD_HINT)
    assert "business-day" in dom.describe() and "dates, text manipulation" not in dom.describe()
    assert "dates, text manipulation" in AgentQADomain().describe()          # default wording unchanged


@pytest.mark.parametrize("seed", [0, 1])
def test_hard_suite_is_deterministic(seed):
    a, b = make_hard_suite(seed=seed), make_hard_suite(seed=seed)
    assert [(t.id, t.input, t.target) for t in a.split("evolve")] == [(t.id, t.input, t.target) for t in b.split("evolve")]
