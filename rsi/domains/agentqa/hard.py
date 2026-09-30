"""Harder AgentQA families, for demonstrations where "write Python" alone is not enough.

The standard families (``generators.py``) fall to one generic trick: have the model write and run code. These
families need more from a harness - reading a structured problem carefully, turning rules into code without
dropping one, and checking the result:

* ``logic``    - 5-house x 5-attribute logic-grid puzzles (naive brute force is ~2.5e10 assignments, far past the tool timeout) with a unique solution (clues must be translated exactly; a
                 brute-force check or a second pass catches mistranslations);
* ``ledger``   - a transaction table with currency conversion, refunds, pending rows, a date window and a
                 rounding rule, asked as one exact figure;
* ``schedule`` - business-day arithmetic over weekends and a holiday list (never seen during practice);
* ``cipher``   - a chain of string transformations applied in order (never seen during practice).

Every question has one exact answer computed here, outside the artifact. :func:`make_hard_suite` builds a suite
with ``logic`` + ``ledger`` as practice families and ``schedule`` + ``cipher`` as the sealed OOD families.
"""
from __future__ import annotations

import datetime as _dt
import itertools
import random
import string

from ...core.tasks import TaskSuite

NAMES = ["Avery", "Blake", "Casey", "Devon", "Emery", "Finley", "Harper", "Jordan", "Logan", "Morgan", "Parker",
         "Quinn", "Reese", "Riley", "Sawyer", "Taylor"]
COLORS = ["red", "green", "blue", "yellow", "white", "orange"]
PETS = ["cat", "dog", "fish", "bird", "horse", "rabbit"]
DRINKS = ["tea", "coffee", "milk", "juice", "water", "cocoa"]
HOBBIES = ["chess", "rowing", "painting", "hiking", "baking", "fencing"]
ATTRS = ("name", "color", "pet", "drink", "hobby")
N_HOUSES = 5


# ---------------------------------------------------------------------------------------------- logic grid
def _phrase(attr: str, v: str) -> str:
    return {"name": v, "color": f"the person in the {v} house", "pet": f"the {v} owner",
            "drink": f"the person who drinks {v}", "hobby": f"the person who does {v}"}[attr]


def _clue_holds(clue, sol) -> bool:
    kind = clue[0]
    if kind == "at":                       # (at, attr, val, house)
        return sol[clue[1]][clue[3]] == clue[2]
    if kind in ("same", "diff"):           # (same, a1, v1, a2, v2)
        h = sol[clue[1]].index(clue[2])
        return (sol[clue[3]][h] == clue[4]) == (kind == "same")
    h1, h2 = sol[clue[1]].index(clue[2]), sol[clue[3]].index(clue[4])
    if kind == "left":                     # immediately left of
        return h1 + 1 == h2
    return abs(h1 - h2) == 1               # next


def _clue_text(clue) -> str:
    kind = clue[0]
    if kind == "at":
        return f"{_phrase(clue[1], clue[2])[0].upper() + _phrase(clue[1], clue[2])[1:]} lives in house {clue[3] + 1}."
    a = _phrase(clue[1], clue[2])
    b = _phrase(clue[3], clue[4])
    a = a[0].upper() + a[1:]
    return {"same": f"{a} is {b}.", "diff": f"{a} is not {b}.",
            "left": f"{a} lives immediately to the left of {b}.", "next": f"{a} lives next to {b}."}[kind]


def _solutions(values: dict, clues: list, cap: int = 2) -> list:
    """Up to ``cap`` assignments satisfying every clue: a small CSP (one variable per attribute value, domain =
    house index, all-different within an attribute), solved by backtracking on the most constrained variable."""
    n = len(values["name"])
    variables = [(a, v) for a in ATTRS for v in values[a]]
    unary: dict = {x: set(range(n)) for x in variables}
    binary: dict = {x: [] for x in variables}
    rels = {"same": lambda p, q: p == q, "diff": lambda p, q: p != q, "left": lambda p, q: p + 1 == q,
            "next": lambda p, q: abs(p - q) == 1}
    for c in clues:
        if c[0] == "at":
            unary[(c[1], c[2])] &= {c[3]}
            continue
        x, y, rel = (c[1], c[2]), (c[3], c[4]), rels[c[0]]
        binary[x].append((y, rel))
        binary[y].append((x, lambda p, q, rel=rel: rel(q, p)))
    out: list = []
    assign: dict = {}

    def domain(x):
        used = {assign[(x[0], v)] for v in values[x[0]] if (x[0], v) in assign}
        return [h for h in sorted(unary[x]) if h not in used
                and all(rel(h, assign[y]) for y, rel in binary[x] if y in assign)]

    def rec():
        if len(out) >= cap:
            return
        free = [x for x in variables if x not in assign]
        if not free:
            out.append({a: [next(v for v in values[a] if assign[(a, v)] == h) for h in range(n)] for a in ATTRS})
            return
        size, _, x, d = min((len(d), i, x, d) for i, x in enumerate(free) for d in [domain(x)])
        if size == 0:
            return
        for h in d:
            assign[x] = h
            rec()
            del assign[x]

    rec()
    return out


def _logic(rng: random.Random):
    n = N_HOUSES
    values = {"name": rng.sample(NAMES, n), "color": rng.sample(COLORS, n), "pet": rng.sample(PETS, n),
              "drink": rng.sample(DRINKS, n), "hobby": rng.sample(HOBBIES, n)}
    truth = {a: rng.sample(values[a], n) for a in ATTRS}       # truth[attr][house] = value
    pool = []
    for a in ATTRS:
        for h in range(n):
            pool.append(("at", a, truth[a][h], h))
    for a1, a2 in itertools.permutations(ATTRS, 2):
        for h in range(n):
            pool.append(("same", a1, truth[a1][h], a2, truth[a2][h]))
            other = rng.choice([x for x in range(n) if x != h])
            pool.append(("diff", a1, truth[a1][h], a2, truth[a2][other]))
        for h in range(n - 1):
            pool.append(("left", a1, truth[a1][h], a2, truth[a2][h + 1]))
            pool.append(("next", a1, truth[a1][h + 1], a2, truth[a2][h]))
    rng.shuffle(pool)
    pool.sort(key=lambda c: c[0] == "at")          # positional clues last: harder puzzles
    clues: list = []
    for c in pool:
        clues.append(c)
        if len(clues) >= 10 and len(_solutions(values, clues)) == 1:
            break
    for c in list(clues):                          # drop redundant clues, keeping the solution unique
        rest = [x for x in clues if x is not c]
        if len(_solutions(values, rest)) == 1:
            clues = rest
    ask_attr = rng.choice(["pet", "drink", "color", "hobby"])
    ask_val = rng.choice(values[ask_attr])
    ans = truth["name"][truth[ask_attr].index(ask_val)]
    q_word = {"pet": f"owns the {ask_val}", "drink": f"drinks {ask_val}", "color": f"lives in the {ask_val} house",
              "hobby": f"does {ask_val}"}
    lines = [f"Five houses stand in a row, numbered 1 to 5 from left to right. Each house has one person, and the "
             f"five people are {', '.join(sorted(values['name']))}. Each person has a different house color "
             f"({', '.join(sorted(values['color']))}), a different pet ({', '.join(sorted(values['pet']))}), "
             f"a different drink ({', '.join(sorted(values['drink']))}) and a different hobby "
             f"({', '.join(sorted(values['hobby']))}).", "Clues:"]
    lines += [f"{i + 1}. {_clue_text(c)}" for i, c in enumerate(clues)]
    lines.append(f"Who {q_word[ask_attr]}? Answer with the name only.")
    return "\n".join(lines), ans, []


# ---------------------------------------------------------------------------------------------- ledger
REGIONS = ["North", "South", "East", "West"]
CUSTOMERS = ["Acme", "Birch", "Cobalt", "Delta", "Ember", "Fjord", "Garnet"]


STATUS_WORDS = {"paid": ["paid", "PAID", "settled"], "refunded": ["refunded", "REFUND", "chargeback"],
                "pending": ["pending", "on hold"]}


def _fmt_date(d: _dt.date, style: int) -> str:
    return [d.isoformat(), d.strftime("%d/%m/%Y"), d.strftime("%b %d %Y")][style]


def _fmt_amount(a: float, cur: str) -> str:
    if cur == "EUR":                                # European notation: 1.234,56
        return f"{a:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".") + " EUR"
    return f"${a:,.2f}"


def _ledger(rng: random.Random):
    """A messy export: three date formats, EUR amounts in European notation, status synonyms, and correction
    rows (a repeated id replaces the earlier row with that id). All rules are stated in the question."""
    rate = round(rng.uniform(1.04, 1.18), 3)
    start = _dt.date(2024, 1, 1) + _dt.timedelta(days=rng.randint(0, 200))
    rows = []
    n = rng.randint(60, 85)
    for i in range(n):
        d = start + _dt.timedelta(days=rng.randint(0, 150))
        cur = "EUR" if rng.random() < 0.4 else "USD"
        rows.append({"id": f"T{1000 + i}", "date": d, "region": rng.choice(REGIONS), "customer": rng.choice(CUSTOMERS),
                     "amount": round(rng.uniform(40, 4800), 2), "currency": cur,
                     "status": rng.choices(["paid", "refunded", "pending"], [0.68, 0.2, 0.12])[0]})
    lines = list(rows)
    for r in rng.sample(rows, rng.randint(4, 8)):  # corrections, appended later in the export
        c = dict(r)
        if rng.random() < 0.5:
            c["amount"] = round(rng.uniform(40, 4800), 2)
        else:
            c["status"] = rng.choice([s for s in STATUS_WORDS if s != r["status"]])
        lines.append(c)
    final = {}
    for r in lines:
        final[r["id"]] = r                             # the last row with an id wins
    rows = list(final.values())
    lo = start + _dt.timedelta(days=rng.randint(10, 50))
    hi = lo + _dt.timedelta(days=rng.randint(40, 80))
    region = rng.choice(REGIONS)
    usd = lambda r: r["amount"] * rate if r["currency"] == "EUR" else r["amount"]
    inwin = [r for r in rows if lo <= r["date"] <= hi]
    kind = rng.randrange(3)
    if kind == 0:
        sel = [usd(r) for r in inwin if r["region"] == region and r["status"] == "paid"]
        q = (f"What is the total of paid transactions in the {region} region dated from {lo} to {hi} inclusive, "
             f"in US dollars? Round the final total to 2 decimals.")
        ans = f"{round(sum(sel) + 1e-9, 2):.2f}"
    elif kind == 1:
        net: dict = {}
        for r in inwin:
            sign = {"paid": 1, "refunded": -1}.get(r["status"], 0)
            net[r["customer"]] = net.get(r["customer"], 0) + sign * usd(r)
        ans = max(sorted(net), key=lambda c: net[c])
        q = (f"For transactions dated from {lo} to {hi} inclusive, compute each customer's net amount in US "
             f"dollars (paid adds, refunded subtracts, pending is ignored). Which customer has the highest net "
             f"amount? Answer with the customer name.")
    else:
        sel = [usd(r) for r in inwin if r["status"] == "paid"]
        q = (f"What is the average value in US dollars of paid transactions dated from {lo} to {hi} inclusive "
             f"(all regions)? Round the average to 2 decimals.")
        ans = f"{round(sum(sel) / len(sel) + 1e-9, 2):.2f}" if sel else "0.00"
    body = "\n".join(f"{r['id']} | {_fmt_date(r['date'], rng.randrange(3))} | {r['region']} | {r['customer']} | "
                     f"{_fmt_amount(r['amount'], r['currency'])} | {rng.choice(STATUS_WORDS[r['status']])}"
                     for r in lines)
    rules = ("Rules for this export: dates appear as YYYY-MM-DD, DD/MM/YYYY or 'Mon DD YYYY'. USD amounts are "
             "written like $1,234.56; EUR amounts use European notation, 1.234,56 EUR, and convert at "
             f"1 EUR = {rate} USD. Status 'paid', 'PAID' and 'settled' mean paid; 'refunded', 'REFUND' and "
             "'chargeback' mean refunded; 'pending' and 'on hold' mean pending. When an id appears more than once, "
             "the LAST row with that id is a correction that replaces the earlier ones.")
    return f"{rules}\nTransactions (id | date | region | customer | amount | status):\n{body}\n\n{q}", ans, []


# ---------------------------------------------------------------------------------------------- OOD: schedule
def _schedule(rng: random.Random):
    start = _dt.date(2025, 1, 1) + _dt.timedelta(days=rng.randint(0, 330))
    horizon = [start + _dt.timedelta(days=i) for i in range(1, 200)]
    weekdays = [d for d in horizon if d.weekday() < 5]
    holidays = sorted(rng.sample(weekdays, rng.randint(6, 11)))
    n = rng.randint(45, 110)
    d, left = start, n
    while True:
        if d.weekday() < 5 and d not in holidays:
            left -= 1
            if left == 0:
                break
        d += _dt.timedelta(days=1)
    q = (f"A job needs {n} business days of work and starts on {start.isoformat()} ({start.strftime('%A')}). "
         f"The start day counts as the first business day if it is a business day. Saturdays, Sundays and these "
         f"holidays are not business days: {', '.join(h.isoformat() for h in holidays)}. On which date is the "
         f"last business day of the job? Answer in YYYY-MM-DD format.")
    return q, d.isoformat(), []


# ---------------------------------------------------------------------------------------------- OOD: cipher
def _cipher(rng: random.Random):
    s = "".join(rng.choice(string.ascii_lowercase) for _ in range(rng.randint(14, 20)))
    ops, x = [], s
    for _ in range(rng.randint(5, 7)):
        k = rng.randrange(7)
        if k == 0:
            x = x[::-1]
            ops.append("reverse the string")
        elif k == 1:
            sh = rng.randint(1, 9)
            x = "".join(chr((ord(c) - 97 + sh) % 26 + 97) for c in x)
            ops.append(f"shift every letter forward in the alphabet by {sh} (z wraps around to a)")
        elif k == 2:
            x = "".join(x[i + 1] + x[i] if i + 1 < len(x) else x[i] for i in range(0, len(x), 2))
            ops.append("swap each pair of adjacent characters (1st with 2nd, 3rd with 4th, ...; a final odd "
                       "character stays in place)")
        elif k == 3:
            r = rng.randint(2, 7)
            x = x[r:] + x[:r]
            ops.append(f"rotate the string left by {r} positions (move the first {r} characters to the end)")
        elif k == 4:
            m = rng.randint(3, 5)
            x = "".join(c for i, c in enumerate(x) if (i + 1) % m)
            ops.append(f"delete every {m}{'rd' if m == 3 else 'th'} character (positions {m}, {2 * m}, ... counting "
                       f"from 1)")
        elif k == 5:
            vw = "aeiou"
            x = "".join(vw[(vw.index(c) + 1) % 5] if c in vw else c for c in x)
            ops.append("replace each vowel with the next vowel in the order a, e, i, o, u (u becomes a)")
        else:
            h = (len(x) + 1) // 2
            a_, b_ = x[:h], x[h:]
            x = "".join(a_[i] + (b_[i] if i < len(b_) else "") for i in range(len(a_)))
            ops.append("split the string into a first half and a second half (the first half gets the extra "
                       "character when the length is odd) and interleave them, starting with the first half")
    steps = "\n".join(f"{i + 1}. {o}" for i, o in enumerate(ops))
    q = f"Start with the string '{s}'. Apply these steps in order:\n{steps}\nWhat is the final string?"
    return q, x, []


HARD_FAMILIES = {"logic": _logic, "ledger": _ledger, "schedule": _schedule, "cipher": _cipher}

OOD_HINT = ("The harness will later be run unchanged on other kinds of multi-step questions with exact answers "
            "(business-day scheduling, chained text transformations), so improvements must be general.")


def make_hard_suite(n_evolve: int = 12, n_holdout: int = 8, n_ood_per_family: int = 4, seed: int = 0) -> TaskSuite:
    """Practice families ``logic`` + ``ledger`` (evolve / holdout), sealed OOD families ``schedule`` + ``cipher``."""
    from .generators import make_suite
    return make_suite(n_evolve=n_evolve, n_holdout=n_holdout, n_ood_per_family=n_ood_per_family,
                      practice_families=("logic", "ledger"), ood_families=("schedule", "cipher"), seed=seed)
