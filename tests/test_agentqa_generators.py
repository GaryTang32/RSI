"""rsi.domains.agentqa.generators: determinism, split structure, independent RNG
streams, and an independent re-derivation of every family's exact answer from
the question text alone (so the graders are provably exact)."""
from __future__ import annotations

import ast
import datetime as dt
import hashlib
import json
import math
import os
import random
import re
import subprocess
import sys
from decimal import ROUND_HALF_UP, Decimal

import pytest

from rsi.core import SealedSplitError
from rsi.domains.agentqa import FAMILIES, decontaminate, make_suite
from rsi.domains.agentqa.generators import _units

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _digits(n: int) -> int:
    return sum(map(int, str(n)))


def _isprime(n: int) -> bool:
    return n >= 2 and all(n % d for d in range(2, math.isqrt(n) + 1))


def solve(q: str) -> str:
    """Answer any generated question from its text, independently of the generators."""
    ints = lambda s: [int(x) for x in re.findall(r"-?\d+", s)]  # noqa: E731
    if m := re.match(r"What is the sum of the decimal digits of (\d+)\^(\d+)\?", q):
        return str(_digits(int(m[1]) ** int(m[2])))
    if m := re.match(r"What is (\d+) mod (\d+)\?", q):
        return str(int(m[1]) % int(m[2]))
    if m := re.match(r"What is the remainder when (\d+)\^(\d+) is divided by (\d+)\?", q):
        return str(pow(int(m[1]), int(m[2]), int(m[3])))
    if "ledger starts at" in q:
        x0, x1, x2, y0, x3, y1, x4, y2, x5 = ints(q.split("ledger starts at", 1)[1])
        return str(x0 + x1 - x2 + y0 * x3 - y1 * x4 + y2 * x5)
    if m := re.match(r"How many 1 bits are in the binary representation of (\d+)\^(\d+)\?", q):
        return str(bin(int(m[1]) ** int(m[2])).count("1"))
    if m := re.match(r"What is the sum of the decimal digits of (\d+)! ", q):
        return str(_digits(math.factorial(int(m[1]))))
    if m := re.match(r"How many prime numbers p satisfy (\d+) <= p <= (\d+)\?", q):
        return str(sum(_isprime(x) for x in range(int(m[1]), int(m[2]) + 1)))
    if m := re.match(r"What is the sum of the decimal digits of C\((\d+),(\d+)\)", q):
        return str(_digits(math.comb(int(m[1]), int(m[2]))))
    if m := re.match(r"How many days are there from (\S+) to (\S+)\?", q):
        return str((dt.date.fromisoformat(m[2]) - dt.date.fromisoformat(m[1])).days)
    if m := re.match(r"(\d{4}-\d\d-\d\d) was a (\w+)\. What day of the week is it (\d+) days later\?", q):
        d0 = dt.date.fromisoformat(m[1])
        assert d0.strftime("%A") == m[2], "the stated weekday must be true"
        return (d0 + dt.timedelta(days=int(m[3]))).strftime("%A")
    if m := re.match(r"How many times does the letter '(\w)' appear in the following text\?\n(.*)", q, re.S):
        return str(m[2].count(m[1]))
    if q.startswith("Reverse the order of these words"):
        return " ".join(reversed(q.split("\n", 1)[1].split()))
    if m := re.match(r"Sort the letters of '(\w+)' alphabetically", q):
        return "".join(sorted(m[1]))
    if "in this list" in q:
        xs = ast.literal_eval(q[q.index("["):])
        if "divisible by 3" in q:
            return str(sum(x for x in xs if x % 3 == 0))
        if "second largest distinct" in q:
            return str(sorted(set(xs))[-2])
        if "divisible by 7" in q:
            return str(sum(1 for x in xs if x % 7 == 0))
    if q.startswith("A warehouse run by"):
        a, b, c, d, e = ints(q)
        return str(a + b - c + d * e)
    if m := re.search(r"buys (\d+) \w+ at \$(\d+) each and pays with (\d+) bills of \$100", q):
        n, p, bills = int(m[1]), int(m[2]), int(m[3])
        return str(bills * 100 - n * p)
    if m := re.match(r"Compute \((\d+) \* (\d+)\) - \((\d+) \* (\d+)\) \+ (\d+)\.", q):
        a, b, c, d, e = map(int, m.groups())
        return str(a * b - c * d + e)
    if m := re.search(r"makes (\d+) of them\. \w+ finds that (\d+)% of them are defective", q):
        total, pct = int(m[1]), int(m[2])
        return str(total - total * pct // 100)
    if m := re.match(r"What is the sum of the digits of (\d+) \* (\d+)\?", q):
        return str(_digits(int(m[1]) * int(m[2])))
    if m := re.match(r"Convert (\d+) feet (\d+) inches to centimeters", q):
        cm = Decimal(int(m[1]) * 12 + int(m[2])) * Decimal("2.54")
        return str(int(cm.quantize(Decimal(1), rounding=ROUND_HALF_UP)))
    if m := re.match(r"How many seconds are in (\d+) hours, (\d+) minutes and (\d+) seconds\?", q):
        return str(int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3]))
    if m := re.match(r"Convert (\d+) kilograms to grams, then divide by 8 and round down", q):
        return str(int(m[1]) * 1000 // 8)
    raise AssertionError(f"unrecognised question: {q[:80]}")


@pytest.mark.parametrize("family", sorted(FAMILIES))
def test_every_family_answer_is_exact(family):
    rng = random.Random(f"answers-{family}")
    kinds = set()
    for _ in range(150):
        q, a, ents = FAMILIES[family](rng)
        assert isinstance(q, str) and isinstance(a, str) and isinstance(ents, list)
        assert solve(q) == a, (family, q[:120], a)
        kinds.add(q[:18])
    assert len(kinds) >= 2                                   # several question kinds per family


def test_units_rounding_is_half_up():
    """Bug fix: float round() gave banker's rounding on exact halves (75 in = 190.5 cm -> 190)."""
    found = 0
    rng = random.Random(1)
    for _ in range(5000):
        q, a, _ = _units(rng)
        m = re.match(r"Convert (\d+) feet (\d+) inches", q)
        if m and ((int(m[1]) * 12 + int(m[2])) * 254) % 100 == 50:
            found += 1
            assert a == solve(q)
    assert found > 0


def test_make_suite_default_structure():
    s = make_suite()
    assert {k: len(v) for k, v in s.splits.items()} == {"evolve": 20, "holdout": 20, "ood": 24, "smoke": 2}
    assert s.families("evolve") == ["numeric"] and s.families("holdout") == ["numeric"]
    assert s.families("ood") == ["dates", "lists", "numbertheory", "strings"]
    assert s.splits["smoke"] == s.splits["evolve"][:2]
    assert s.name == "agentqa-s0" and len(s) == 64
    assert all(tid.startswith(split + "-") for split in ("evolve", "holdout", "ood") for tid in s.splits[split])
    for split in ("holdout", "ood"):
        with pytest.raises(SealedSplitError):
            s.split(split)
    for t in s.split("evolve"):
        assert solve(t.input) == t.target
    ledger = [t for t in s.tasks.values() if "ledger" in t.input]
    assert ledger and all(t.meta["entities"] and t.meta["entities"][0] in t.input for t in ledger)


def test_make_suite_options():
    s = make_suite(n_evolve=6, n_holdout=0, n_ood_per_family=2, practice_families=("arith", "units"),
                   ood_families=("dates",), n_val=4, seed=5)
    assert [s.get(i).family for i in s.splits["evolve"]] == ["arith", "units"] * 3
    assert len(s.splits["val"]) == 4 and s.splits["holdout"] == [] and len(s.splits["ood"]) == 2
    assert not s.is_sealed("val")


def _fingerprint(s) -> str:
    return hashlib.sha256(json.dumps([[t.id, t.input, t.target] for t in s.tasks.values()] +
                                     [s.splits], sort_keys=True).encode()).hexdigest()


def test_make_suite_deterministic_and_seeded():
    assert _fingerprint(make_suite(seed=0)) == _fingerprint(make_suite(seed=0))
    assert _fingerprint(make_suite(seed=0)) != _fingerprint(make_suite(seed=1))
    a, b = make_suite(seed=0), make_suite(seed=1)
    assert a.get("evolve-numeric-000").input != b.get("evolve-numeric-000").input


def test_splits_use_independent_rng_streams():
    base = make_suite()
    grown = make_suite(n_evolve=30, n_holdout=5, n_ood_per_family=8, n_val=3)
    for tid in base.splits["evolve"]:
        assert grown.get(tid).input == base.get(tid).input          # evolve prefix unchanged
    for tid in grown.splits["holdout"]:
        assert grown.get(tid).input == base.get(tid).input
    for tid in base.splits["ood"]:
        assert grown.get(tid).input == base.get(tid).input


def test_default_suite_overlap_and_decontaminate():
    """Finding: factorial digit sums have only 46 questions, so seed 0 repeats one evolve
    question in holdout (and seed 1 repeats one inside evolve). make_suite is left
    unchanged for reproducibility; decontaminate() is the opt-in fix."""
    def overlap(s):
        ev = [s.tasks[t].input for t in s.splits["evolve"]]
        ho = {s.tasks[t].input for t in s.splits["holdout"]}
        return len(set(ev) & ho), len(ev) - len(set(ev))

    assert overlap(make_suite(seed=0)) == (1, 0)
    assert overlap(make_suite(seed=1)) == (0, 1)
    for seed in (0, 1):
        raw = make_suite(seed=seed)
        clean = decontaminate(raw)
        assert overlap(clean) == (0, 0)
        assert len(clean.splits["evolve"]) + len(clean.splits["holdout"]) == 39
        assert clean.splits["ood"] == raw.splits["ood"]
        assert clean.is_sealed("holdout") and clean.is_sealed("ood") and not clean.is_sealed("evolve")
        assert set(clean.splits["smoke"]) <= set(clean.splits["evolve"])
    unsealed = make_suite()
    unsealed.unseal("holdout")
    assert not decontaminate(unsealed).is_sealed("holdout") and decontaminate(unsealed).is_sealed("ood")


def test_make_suite_stable_across_processes():
    """String-seeded RNGs must not depend on PYTHONHASHSEED."""
    code = ("import sys, json, hashlib; sys.path.insert(0, %r)\n"
            "from rsi.domains.agentqa import make_suite\n"
            "s = make_suite()\n"
            "print(hashlib.sha256(json.dumps([[t.id, t.input, t.target] for t in s.tasks.values()] + [s.splits], "
            "sort_keys=True).encode()).hexdigest())") % ROOT
    env = dict(os.environ, PYTHONHASHSEED="12345")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=60)
    assert out.stdout.strip() == _fingerprint(make_suite())
