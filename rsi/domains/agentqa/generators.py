"""Procedurally generated question families with exact answers.

Families are chosen so that *generic* harness improvements (use a Python tool,
state the answer format, verify) help on every family, while *suite-specific*
tricks (memorising practice answers) help only on the practice split. That is
the property needed to observe overfitting and transfer.

Calibrated against Claude Haiku 4.5 (no tools): the ``numeric`` practice family
mixes kinds the model always solves (modular exponent, ledger chains) with kinds
it essentially never solves by mental arithmetic (bit counts of big powers,
factorial digit sums), giving a ~40% baseline with large headroom. Never-seen
families: ``dates`` (weak), ``numbertheory`` (0% without tools), ``strings`` and
``lists`` (solved, but only with thousands of reasoning tokens - so a tool-using
harness is both more accurate and cheaper).

Legacy easy families ``arith`` and ``units`` remain available.
"""
from __future__ import annotations

import datetime as _dt
import math
import random
import string
from typing import Callable

from ...core.tasks import Task, TaskSuite

NAMES = ["Avery", "Blake", "Casey", "Devon", "Emery", "Finley", "Harper", "Jordan", "Kendall", "Logan", "Morgan",
         "Parker", "Quinn", "Reese", "Riley", "Rowan", "Sawyer", "Taylor"]
GOODS = ["crates", "boxes", "pallets", "bottles", "lamps", "chairs", "tablets", "bicycles", "kettles", "helmets"]
WORDS = ["river", "carrot", "mirror", "terror", "barrier", "orbit", "parrot", "error", "reward", "harbor", "mortar",
         "stream", "garden", "border", "cherry", "arrow", "rover", "clover", "marker", "sparrow", "tractor"]


def _arith(rng: random.Random):
    kind = rng.randrange(5)
    name = rng.choice(NAMES)
    good = rng.choice(GOODS)
    if kind == 0:
        a, b, c = rng.randint(10000, 99999), rng.randint(1000, 9999), rng.randint(1000, 9999)
        d, e = rng.randint(12, 97), rng.randint(113, 987)
        q = (f"A warehouse run by {name} starts with {a} {good}. It receives {b} {good} on Monday and ships out "
             f"{c} {good} on Tuesday. Then it receives {d} shipments of {e} {good} each. How many {good} are in the "
             f"warehouse now?")
        ans = a + b - c + d * e
    elif kind == 1:
        n, p = rng.randint(13, 89), rng.randint(17, 389)
        m = (n * p) // 100 + rng.randint(1, 9)
        q = (f"{name} buys {n} {good} at ${p} each and pays with {m} bills of $100. How much change, in dollars, "
             f"does {name} receive?")
        ans = m * 100 - n * p
    elif kind == 2:
        a, b, c, d = rng.randint(1000, 9999), rng.randint(100, 999), rng.randint(1000, 9999), rng.randint(10, 99)
        e = rng.randint(10000, 99999)
        q = f"Compute ({a} * {b}) - ({c} * {d}) + {e}. Give the exact integer."
        ans = a * b - c * d + e
    elif kind == 3:
        per_hour, hours, days = rng.randint(113, 947), rng.randint(6, 23), rng.randint(11, 59)
        pct = rng.choice([2, 4, 5, 8, 10, 20, 25])
        total = per_hour * hours * days
        total -= total % 100  # make the percentage exact
        q = (f"A factory produces {good} and over a season makes {total} of them. {name} finds that {pct}% of them "
             f"are defective. How many {good} are NOT defective?")
        ans = total - total * pct // 100
    else:
        a, b = rng.randint(1000, 9999), rng.randint(1000, 9999)
        q = f"What is the sum of the digits of {a} * {b}?"
        ans = sum(int(ch) for ch in str(a * b))
    return q, str(ans), [name]


def _numeric(rng: random.Random):
    kind = rng.randrange(6)
    name = rng.choice(NAMES)
    if kind == 0:
        a, k = rng.randint(13, 97), rng.randint(9, 15)
        q, ans = f"What is the sum of the decimal digits of {a}^{k}?", sum(map(int, str(a ** k)))
    elif kind == 1:
        a, m = rng.randint(10 ** 13, 10 ** 14), rng.randint(101, 997)
        q, ans = f"What is {a} mod {m}?", a % m
    elif kind == 2:
        b, n, m = rng.randint(3, 19), rng.randint(100, 900), rng.randint(101, 997)
        q, ans = f"What is the remainder when {b}^{n} is divided by {m}?", pow(b, n, m)
    elif kind == 3:
        xs = [rng.randint(10000, 99999) for _ in range(6)]
        ys = [rng.randint(11, 99) for _ in range(3)]
        q = (f"{name}'s ledger starts at {xs[0]}. Add {xs[1]}, subtract {xs[2]}, add {ys[0]} payments of {xs[3]}, "
             f"subtract {ys[1]} refunds of {xs[4]}, and add {ys[2]} deposits of {xs[5]}. What is the final balance?")
        ans = xs[0] + xs[1] - xs[2] + xs[3] * ys[0] - xs[4] * ys[1] + xs[5] * ys[2]
    elif kind == 4:
        a, k = rng.randint(11, 99), rng.randint(8, 14)
        q, ans = f"How many 1 bits are in the binary representation of {a}^{k}?", bin(a ** k).count("1")
    else:
        n = rng.randint(25, 70)
        q, ans = f"What is the sum of the decimal digits of {n}! ({n} factorial)?", sum(map(int, str(math.factorial(n))))
    return q, str(ans), [name] if kind == 3 else []


def _isprime(n: int) -> bool:
    if n < 2:
        return False
    i = 2
    while i * i <= n:
        if n % i == 0:
            return False
        i += 1
    return True


def _numbertheory(rng: random.Random):
    if rng.random() < 0.5:
        n = rng.randint(1000, 90000)
        return f"How many prime numbers p satisfy {n} <= p <= {n + 150}?", str(sum(_isprime(x) for x in range(n, n + 151))), []
    n, k = rng.randint(40, 90), rng.randint(9, 20)
    return (f"What is the sum of the decimal digits of C({n},{k}), the binomial coefficient '{n} choose {k}'?",
            str(sum(map(int, str(math.comb(n, k))))), [])


def _dates(rng: random.Random):
    base = _dt.date(2000, 1, 1) + _dt.timedelta(days=rng.randint(0, 9000))
    if rng.random() < 0.5:
        other = base + _dt.timedelta(days=rng.randint(40, 1500))
        q = f"How many days are there from {base.isoformat()} to {other.isoformat()}? (Count the difference in days.)"
        ans = str((other - base).days)
    else:
        n = rng.randint(50, 2000)
        target = base + _dt.timedelta(days=n)
        q = (f"{base.isoformat()} was a {base.strftime('%A')}. What day of the week is it {n} days later? "
             f"Answer with the weekday name.")
        ans = target.strftime("%A")
    return q, ans, []


def _strings(rng: random.Random):
    kind = rng.randrange(3)
    words = [rng.choice(WORDS) for _ in range(rng.randint(30, 45))]
    if kind == 0:
        letter = rng.choice("reoa")
        text = " ".join(words)
        q = f"How many times does the letter '{letter}' appear in the following text?\n{text}"
        ans = str(text.count(letter))
    elif kind == 1:
        w = words[: rng.randint(12, 18)]
        q = "Reverse the order of these words and give them separated by single spaces:\n" + " ".join(w)
        ans = " ".join(reversed(w))
    else:
        s = "".join(rng.choice(string.ascii_lowercase) for _ in range(rng.randint(14, 22)))
        q = f"Sort the letters of '{s}' alphabetically and give the resulting string."
        ans = "".join(sorted(s))
    return q, ans, []


def _lists(rng: random.Random):
    xs = [rng.randint(-900, 999) for _ in range(rng.randint(45, 60))]
    kind = rng.randrange(3)
    if kind == 0:
        q = f"What is the sum of the numbers in this list that are divisible by 3? {xs}"
        ans = sum(x for x in xs if x % 3 == 0)
    elif kind == 1:
        q = f"What is the second largest distinct number in this list? {xs}"
        ans = sorted(set(xs))[-2]
    else:
        q = f"How many numbers in this list are divisible by 7? {xs}"
        ans = sum(1 for x in xs if x % 7 == 0)
    return q, str(ans), []


def _units(rng: random.Random):
    kind = rng.randrange(3)
    if kind == 0:
        ft, inch = rng.randint(3, 40), rng.randint(0, 11)
        q = f"Convert {ft} feet {inch} inches to centimeters (1 inch = 2.54 cm exactly). Round to the nearest integer."
        ans = ((ft * 12 + inch) * 254 + 50) // 100  # exact, halves round up (float round() gave 190 for 190.5)
    elif kind == 1:
        h, m, s = rng.randint(1, 30), rng.randint(0, 59), rng.randint(0, 59)
        q = f"How many seconds are in {h} hours, {m} minutes and {s} seconds?"
        ans = h * 3600 + m * 60 + s
    else:
        kg = rng.randint(3, 900)
        q = f"Convert {kg} kilograms to grams, then divide by 8 and round down to an integer."
        ans = (kg * 1000) // 8
    return q, str(ans), []


FAMILIES: dict[str, Callable[[random.Random], tuple[str, str, list]]] = {
    "numeric": _numeric, "dates": _dates, "numbertheory": _numbertheory, "strings": _strings, "lists": _lists,
    "arith": _arith, "units": _units,
}


def make_suite(
    n_evolve: int = 20,
    n_holdout: int = 20,
    n_ood_per_family: int = 6,
    practice_families: tuple[str, ...] = ("numeric",),
    ood_families: tuple[str, ...] = ("dates", "numbertheory", "strings", "lists"),
    n_val: int = 0,
    seed: int = 0,
) -> TaskSuite:
    """Build the standard suite: evolve/holdout drawn from the practice families,
    ood from the never-seen families. Each split uses an independent RNG stream."""
    tasks: list[Task] = []
    splits: dict[str, list[str]] = {"evolve": [], "holdout": [], "ood": []}
    if n_val:
        splits["val"] = []

    def add(split: str, fam: str, rng: random.Random, i: int):
        q, a, ents = FAMILIES[fam](rng)
        tid = f"{split}-{fam}-{i:03d}"
        tasks.append(Task(tid, q, a, fam, {"entities": ents}))
        splits[split].append(tid)

    for split, n in (("evolve", n_evolve), ("val", n_val), ("holdout", n_holdout)):
        if not n:
            continue
        rng = random.Random(f"{seed}-{split}")
        for i in range(n):
            add(split, practice_families[i % len(practice_families)], rng, i)
    for fam in ood_families:
        rng = random.Random(f"{seed}-ood-{fam}")
        for i in range(n_ood_per_family):
            add("ood", fam, rng, i)
    splits["smoke"] = splits["evolve"][:2]
    return TaskSuite(tasks, splits, name=f"agentqa-s{seed}")


def decontaminate(suite: TaskSuite) -> TaskSuite:
    """Copy of ``suite`` without repeated questions.

    Some question kinds have a small space (e.g. factorial digit sums: 46
    values), so independently drawn splits can share a question - with seed 0
    one holdout task repeats an evolve task. This drops tasks whose question text
    already appeared in a decision split (evolve, then train/val) or earlier in
    the same split; ``smoke`` keeps its surviving evolve tasks and sealed splits
    stay sealed. Opt-in, so the default :func:`make_suite` output (and every
    result built on it) is unchanged.
    """
    decision = [s for s in ("evolve", "train", "val") if s in suite.splits]
    order = decision + [s for s in suite.splits if s not in decision and s != "smoke"]
    seen_decision: set = set()
    splits: dict[str, list[str]] = {}
    for name in order:
        seen_here: set = set()
        keep = []
        for tid in suite.splits[name]:
            q = suite.tasks[tid].input
            key = q if isinstance(q, str) else repr(q)
            if key in seen_here or key in seen_decision:
                continue
            seen_here.add(key)
            keep.append(tid)
        splits[name] = keep
        if name in decision:
            seen_decision |= seen_here
    if "smoke" in suite.splits:
        kept = set(splits.get("evolve", []))
        splits["smoke"] = [t for t in suite.splits["smoke"] if t in kept]
    out = TaskSuite(suite.tasks.values(), {k: splits[k] for k in suite.splits}, name=suite.name)
    for name in suite.splits:
        if suite.is_sealed(name):
            out.seal(name)          # includes custom splits sealed with suite.seal(...)
        else:
            out.unseal(name)
    return out
