"""The harder AgentQA families: every task has exactly one answer, re-derived here from the question text alone."""
from __future__ import annotations

import datetime as dt
import itertools
import re

import pytest

from rsi.domains.agentqa import AgentQADomain
from rsi.domains.agentqa.hard import OOD_HINT, make_hard_suite


def _tasks(fam: str):
    s = make_hard_suite(n_evolve=16, n_holdout=8, n_ood_per_family=8, seed=3)
    for split in ("holdout", "ood"):
        s.unseal(split)
    return [t for split in ("evolve", "holdout", "ood") for t in s.split(split) if t.family == fam]


def _solve_logic(q: str) -> list[str]:
    head = q.split("Clues:")[0]
    names = re.search(r"people are ([^.]+)\.", head).group(1).split(", ")
    lists = re.findall(r"\(([^)]+)\)", head)
    vals = {"name": names, "color": lists[0].split(", "), "pet": lists[1].split(", "), "drink": lists[2].split(", ")}

    def ref(phrase: str):
        phrase = phrase.strip().rstrip(".")
        for a, pat in (("color", r"the person in the (\w+) house"), ("pet", r"the (\w+) owner"),
                       ("drink", r"the person who drinks (\w+)")):
            m = re.fullmatch(pat, phrase, re.I)
            if m:
                return a, m.group(1).lower()
        return "name", phrase

    clues = re.findall(r"^\d+\. (.+)$", q, re.M)
    sols = []
    for combo in itertools.product(*(itertools.permutations(vals[a]) for a in ("name", "color", "pet", "drink"))):
        sol = dict(zip(("name", "color", "pet", "drink"), combo))
        pos = lambda a, v: sol[a].index(v)
        good = True
        for c in clues:
            if m := re.fullmatch(r"(.+) lives in house (\d)\.", c):
                a, v = ref(m.group(1)); good = sol[a][int(m.group(2)) - 1] == v
            elif m := re.fullmatch(r"(.+) lives immediately to the left of (.+)\.", c):
                (a1, v1), (a2, v2) = ref(m.group(1)), ref(m.group(2)); good = pos(a1, v1) + 1 == pos(a2, v2)
            elif m := re.fullmatch(r"(.+) lives next to (.+)\.", c):
                (a1, v1), (a2, v2) = ref(m.group(1)), ref(m.group(2)); good = abs(pos(a1, v1) - pos(a2, v2)) == 1
            elif m := re.fullmatch(r"(.+) is not (.+)\.", c):
                (a1, v1), (a2, v2) = ref(m.group(1)), ref(m.group(2)); good = pos(a1, v1) != pos(a2, v2)
            elif m := re.fullmatch(r"(.+) is (.+)\.", c):
                (a1, v1), (a2, v2) = ref(m.group(1)), ref(m.group(2)); good = pos(a1, v1) == pos(a2, v2)
            else:
                raise AssertionError(c)
            if not good:
                break
        if good:
            sols.append(sol)
    m = re.search(r"Who (owns the (\w+)|drinks (\w+)|lives in the (\w+) house)\?", q)
    a, v = (("pet", m.group(2)) if m.group(2) else ("drink", m.group(3)) if m.group(3) else ("color", m.group(4)))
    return [s["name"][s[a].index(v)] for s in sols]


def test_logic_puzzles_have_one_solution_matching_the_target():
    ts = _tasks("logic")
    assert len(ts) >= 8
    for t in ts:
        assert _solve_logic(t.input) == [t.target], t.id


def test_ledger_targets_recompute_from_the_table():
    for t in _tasks("ledger"):
        q = t.input
        rate = float(re.search(r"1 EUR = ([\d.]+) USD", q).group(1))
        rows = re.findall(r"^T\d+ \| (\S+) \| (\w+) \| (\w+) \| ([\d,.]+) (EUR|USD) \| (\w+)$", q, re.M)
        lo, hi = re.search(r"from (\S+) to (\S+) inclusive", q).groups()
        rows = [(d, reg, cu, float(a.replace(",", "")) * (rate if c == "EUR" else 1), st) for d, reg, cu, a, c, st in rows
                if lo <= d <= hi]
        if "Which customer" in q:
            net = {}
            for _, _, cu, v, st in rows:
                net[cu] = net.get(cu, 0) + (v if st == "paid" else -v if st == "refunded" else 0)
            got = max(sorted(net), key=lambda k: net[k])
        elif "average" in q:
            vs = [v for *_, v, st in rows if st == "paid"]
            got = f"{round(sum(vs) / len(vs) + 1e-9, 2):.2f}"
        else:
            reg = re.search(r"in the (\w+) region", q).group(1)
            got = f"{round(sum(v for _, r, _, v, st in rows if r == reg and st == 'paid') + 1e-9, 2):.2f}"
        assert got == t.target, t.id


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
            elif m := re.match(r"delete every (\d+)th", step):
                k = int(m.group(1)); x = "".join(c for i, c in enumerate(x) if (i + 1) % k)
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
