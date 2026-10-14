"""The harder AgentQA families: every task has exactly one answer, re-derived here from the question text alone."""
from __future__ import annotations

import datetime as dt
import itertools
import random
import re

import numpy as np
import pytest

from rsi.domains.agentqa import AgentQADomain
from rsi.domains.agentqa.hard import OOD_HINT, _cipher, make_hard_suite


#: a 34-task suite with every family, plus the suite the live demo ran on (experiments/demo/live_demo.py)
SUITES = (dict(n_evolve=12, n_holdout=6, n_ood_per_family=8, seed=3),
          dict(n_evolve=8, n_holdout=6, n_ood_per_family=3, seed=0))


def _tasks(fam: str):
    out = []
    for kw in SUITES:
        s = make_hard_suite(**kw)
        for split in ("holdout", "ood"):
            s.unseal(split)
        out += [t for split in ("evolve", "holdout", "ood") for t in s.split(split) if t.family == fam]
    return out


ATTR_ORDER = ("name", "color", "pet", "drink", "hobby")


def _solve_logic(q: str) -> list[str]:
    """Independent solver: parses the clues from the question text and searches attribute by attribute (every
    permutation of one attribute's values over the houses, most-constrained attribute first), checking each clue
    once all the attributes it mentions are placed. The generator instead backtracks over single values."""
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
    # attribute by attribute (the generator solves per value): try every permutation of an attribute's values
    # over the houses, most-constrained attribute first, and check each clue once all its attributes are placed
    need = [{r[0] for r in refs} for refs, _ in checks]
    order = sorted(ATTR_ORDER, key=lambda a: -sum(a in s for s in need))
    sols, where = [], {}

    def ok(done: set) -> bool:
        for (refs, f), s in zip(checks, need):
            if s <= done and not f([where[r] for r in refs]):
                return False
        return True

    def place(i: int):
        if i == len(order):
            sols.append(dict(where))
            return
        a = order[i]
        for perm in itertools.permutations(range(n)):
            for v, h in zip(vals[a], perm):
                where[(a, v)] = h
            if ok(set(order[: i + 1])):
                place(i + 1)
        for v in vals[a]:
            where.pop((a, v), None)

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
        start = re.search(r"starts on (\S+)", t.input).group(1)
        hol = re.findall(r"\d{4}-\d\d-\d\d", t.input.split("holidays")[1])
        # numpy's business-day calendar, not the generator's loop: the start day counts if it is a business day
        first = np.busday_offset(start, 0, roll="forward", holidays=hol)
        assert str(np.busday_offset(first, n - 1, roll="forward", holidays=hol)) == t.target, t.id
    seen = set()
    for t in _tasks("cipher"):
        assert _run_cipher(t.input, seen) == t.target, t.id
    assert seen == set(CIPHER_OPS), f"cipher ops never generated: {set(CIPHER_OPS) - seen}"


def test_cipher_rotation_wraps_when_the_string_is_shorter():
    # deletions can shrink the string below the rotation count; "rotate left by r" then wraps around (r mod len)
    for i in range(3000):
        q, target, _ = _cipher(random.Random(f"e-{i}"))
        if re.search(r"rotate the string left by (\d+)", q) and _run_cipher(q, set(), check_short_rotation=True):
            assert _run_cipher(q, set()) == target
            return
    pytest.skip("no short-string rotation in the sampled tasks")


CIPHER_OPS = ("reverse", "shift", "swap", "rotate", "delete", "vowel", "interleave")


def _run_cipher(q: str, seen: set, check_short_rotation: bool = False):
    """Apply the steps as the question words them. With ``check_short_rotation`` return whether any rotation
    count exceeded the string length instead."""
    short = False
    x = re.search(r"string '(\w+)'", q).group(1)
    for step in re.findall(r"^\d+\. (.+)$", q, re.M):
        if step.startswith("reverse"):
            seen.add("reverse"); x = x[::-1]
        elif m := re.match(r"shift every letter forward in the alphabet by (\d+)", step):
            seen.add("shift"); x = "".join(chr((ord(c) - 97 + int(m.group(1))) % 26 + 97) for c in x)
        elif step.startswith("swap"):
            seen.add("swap"); x = "".join(x[i:i + 2][::-1] for i in range(0, len(x), 2))
        elif m := re.match(r"rotate the string left by (\d+)", step):
            seen.add("rotate"); short = short or int(m.group(1)) > len(x)
            r = int(m.group(1)) % len(x) if x else 0; x = x[r:] + x[:r]
        elif m := re.match(r"delete every (\d+)(?:rd|th)", step):
            seen.add("delete"); k = int(m.group(1)); x = "".join(c for i, c in enumerate(x) if (i + 1) % k)
        elif step.startswith("replace each vowel"):
            seen.add("vowel"); x = x.translate(str.maketrans("aeiou", "eioua"))
        elif step.startswith("split the string"):
            seen.add("interleave"); h = (len(x) + 1) // 2
            x = "".join(p + q for p, q in itertools.zip_longest(x[:h], x[h:], fillvalue=""))
        else:
            raise AssertionError(step)
    return short if check_short_rotation else x


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
