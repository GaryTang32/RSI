"""Kata definitions: 5 classes x 5 small Python functions (EvoMap spec §9.3 Tier 2).

Each kata has a signature + description (public), 2 public smoke asserts (weak:
they pass for the buggy variants too), hidden asserts that exercise the class's
pitfall, a correct implementation and one or two pitfall variants (used by the
offline simulated solver). Classes: off-by-one boundaries, unicode
normalization, date handling, decimal rounding, retry/backoff.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Kata:
    id: str
    cls: str
    fn: str
    signature: str
    description: str
    public: tuple
    hidden: tuple
    correct: str
    buggy: tuple
    keywords: tuple = ()


def K(id, cls, fn, sig, desc, public, hidden, correct, buggy, kw=()):
    return Kata(id, cls, fn, sig, desc, tuple(public), tuple(hidden), correct.strip() + "\n",
                tuple(b.strip() + "\n" for b in buggy), tuple(kw))


CLASS_KEYWORDS = {
    "boundaries": ("boundaries", "ranges"),
    "unicode": ("unicode", "text-equality"),
    "dates": ("dates", "calendar"),
    "rounding": ("rounding", "money"),
    "retry": ("retry", "backoff"),
}

KATAS = [
    # ------------------------------------------------------------------ boundaries
    K("count_in_range", "boundaries", "count_in_range", "def count_in_range(nums, lo, hi):",
      "Return how many values v in nums satisfy lo <= v <= hi (both bounds inclusive).",
      ["count_in_range([1, 5, 9], 0, 4) == 1", "count_in_range([], 0, 10) == 0"],
      ["count_in_range([1, 5, 9], 1, 9) == 3", "count_in_range([3, 3, 3], 3, 3) == 3", "count_in_range([2, 4], 3, 3) == 0",
       "count_in_range([-2, 0, 2], -2, 0) == 2"],
      "def count_in_range(nums, lo, hi):\n    return sum(1 for v in nums if lo <= v <= hi)",
      ["def count_in_range(nums, lo, hi):\n    return sum(1 for v in nums if lo <= v < hi)"], ["counting"]),
    K("chunk", "boundaries", "chunk", "def chunk(lst, n):",
      "Split lst into consecutive chunks of size n; the last chunk may be shorter. Return a list of lists.",
      ["chunk([1, 2, 3, 4], 2) == [[1, 2], [3, 4]]", "chunk([], 3) == []"],
      ["chunk([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]", "chunk([1], 5) == [[1]]",
       "chunk(list(range(7)), 3) == [[0, 1, 2], [3, 4, 5], [6]]"],
      "def chunk(lst, n):\n    return [lst[i:i + n] for i in range(0, len(lst), n)]",
      ["def chunk(lst, n):\n    return [lst[i * n:(i + 1) * n] for i in range(len(lst) // n)]"], ["lists"]),
    K("pages_needed", "boundaries", "pages_needed", "def pages_needed(items, per_page):",
      "Return the number of pages needed to show `items` items with `per_page` items per page (0 items -> 0 pages).",
      ["pages_needed(10, 5) == 2", "pages_needed(0, 5) == 0"],
      ["pages_needed(11, 5) == 3", "pages_needed(1, 10) == 1", "pages_needed(20, 7) == 3"],
      "def pages_needed(items, per_page):\n    return -(-items // per_page)",
      ["def pages_needed(items, per_page):\n    return items // per_page"], ["pagination"]),
    K("inclusive_sum", "boundaries", "inclusive_sum", "def inclusive_sum(a, b):",
      "Return the sum of all integers from a to b inclusive (a <= b).",
      ["inclusive_sum(5, 5) == 5 or inclusive_sum(5, 5) == 0", "isinstance(inclusive_sum(1, 3), int)"],
      ["inclusive_sum(1, 3) == 6", "inclusive_sum(5, 5) == 5", "inclusive_sum(-2, 2) == 0", "inclusive_sum(10, 12) == 33"],
      "def inclusive_sum(a, b):\n    return sum(range(a, b + 1))",
      ["def inclusive_sum(a, b):\n    return sum(range(a, b))"], ["arithmetic"]),
    K("last_n", "boundaries", "last_n", "def last_n(lst, n):",
      "Return the last n elements of lst as a list (n >= 0; n == 0 returns []; n > len(lst) returns the whole list).",
      ["last_n([1, 2, 3], 2) == [2, 3]", "last_n([], 2) == []"],
      ["last_n([1, 2, 3], 0) == []", "last_n([1, 2, 3], 5) == [1, 2, 3]", "last_n([4, 5], 1) == [5]"],
      "def last_n(lst, n):\n    return list(lst[max(0, len(lst) - n):]) if n > 0 else []",
      ["def last_n(lst, n):\n    return list(lst[-n:])"], ["slicing"]),
    # ------------------------------------------------------------------ unicode
    K("same_text", "unicode", "same_text", "def same_text(a, b):",
      "Return True if strings a and b are the same text, ignoring case and Unicode representation differences.",
      ["same_text('Hello', 'hello')", "not same_text('a', 'b')"],
      ["same_text('Caf\\u00e9', 'cafe\\u0301')", "same_text('STRASSE', 'stra\\u00dfe')", "not same_text('cafe', 'caf\\u00e9')"],
      "import unicodedata\n\ndef same_text(a, b):\n    n = lambda s: unicodedata.normalize('NFC', s).casefold()\n"
      "    return unicodedata.normalize('NFC', n(a)) == unicodedata.normalize('NFC', n(b))",
      ["def same_text(a, b):\n    return a.lower() == b.lower()"], ["comparison"]),
    K("count_char", "unicode", "count_char", "def count_char(s, ch):",
      "Return how many times the character ch (a single user-perceived character) occurs in s, regardless of how "
      "the characters are encoded (precomposed or combining sequences).",
      ["count_char('banana', 'a') == 3", "count_char('', 'x') == 0"],
      ["count_char('cafe\\u0301 caf\\u00e9', '\\u00e9') == 2", "count_char('e\\u0301e\\u0301', 'e\\u0301') == 2",
       "count_char('abc', 'd') == 0"],
      "import unicodedata\n\ndef count_char(s, ch):\n    n = lambda x: unicodedata.normalize('NFC', x)\n"
      "    return n(s).count(n(ch))",
      ["def count_char(s, ch):\n    return s.count(ch)"], ["counting"]),
    K("strip_accents", "unicode", "strip_accents", "def strip_accents(s):",
      "Remove accents/diacritics from letters but keep the base letters and all other characters.",
      ["strip_accents('abc') == 'abc'", "strip_accents('') == ''"],
      ["strip_accents('caf\\u00e9') == 'cafe'", "strip_accents('na\\u00efve r\\u00e9sum\\u00e9') == 'naive resume'",
       "strip_accents('\\u00c5ngstr\\u00f6m') == 'Angstrom'"],
      "import unicodedata\n\ndef strip_accents(s):\n    d = unicodedata.normalize('NFD', s)\n"
      "    return unicodedata.normalize('NFC', ''.join(c for c in d if not unicodedata.combining(c)))",
      ["def strip_accents(s):\n    return s.encode('ascii', 'ignore').decode()"], ["cleanup"]),
    K("is_palindrome", "unicode", "is_palindrome", "def is_palindrome(s):",
      "Return True if s reads the same backwards considering only letters, ignoring case (including special case "
      "foldings) and Unicode representation.",
      ["is_palindrome('Racecar')", "not is_palindrome('abc')"],
      ["is_palindrome('A man, a plan, a canal: Panama')", "is_palindrome('\\u00c9t\\u00e9')",
       "is_palindrome('Ss\\u00df')"],
      "import unicodedata\n\ndef is_palindrome(s):\n    t = unicodedata.normalize('NFC', s).casefold()\n"
      "    t = [c for c in unicodedata.normalize('NFC', t) if c.isalpha()]\n    return t == t[::-1]",
      ["def is_palindrome(s):\n    t = [c.lower() for c in s if c.isalpha()]\n    return t == t[::-1]"], ["strings"]),
    K("visible_length", "unicode", "visible_length", "def visible_length(s):",
      "Return the number of user-visible characters in s, where a base letter followed by combining marks counts "
      "once.",
      ["visible_length('abc') == 3", "visible_length('') == 0"],
      ["visible_length('e\\u0301') == 1", "visible_length('cafe\\u0301') == 4", "visible_length('a\\u0300\\u0301b') == 2"],
      "import unicodedata\n\ndef visible_length(s):\n    return sum(1 for c in unicodedata.normalize('NFC', s) "
      "if not unicodedata.combining(c))",
      ["def visible_length(s):\n    return len(s)"], ["length"]),
    # ------------------------------------------------------------------ dates
    K("days_between", "dates", "days_between", "def days_between(d1, d2):",
      "Given two dates as 'YYYY-MM-DD' strings, return the absolute number of days between them.",
      ["days_between('2021-01-01', '2021-01-11') == 10", "days_between('2021-05-05', '2021-05-05') == 0"],
      ["days_between('2020-02-28', '2020-03-01') == 2", "days_between('2019-12-31', '2021-01-01') == 367",
       "days_between('2024-03-01', '2023-03-01') == 366"],
      "from datetime import date\n\ndef days_between(d1, d2):\n    return abs((date.fromisoformat(d2) - "
      "date.fromisoformat(d1)).days)",
      ["def days_between(d1, d2):\n    def n(d):\n        y, m, dd = map(int, d.split('-'))\n"
       "        return y * 365 + (m - 1) * 30 + dd\n    return abs(n(d2) - n(d1))"], ["duration"]),
    K("is_leap", "dates", "is_leap", "def is_leap(year):",
      "Return True if `year` is a leap year in the Gregorian calendar.",
      ["is_leap(2024)", "not is_leap(2023)"],
      ["not is_leap(1900)", "is_leap(2000)", "not is_leap(2100)", "is_leap(1996)"],
      "def is_leap(year):\n    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)",
      ["def is_leap(year):\n    return year % 4 == 0"], ["leapyear"]),
    K("parse_us_date", "dates", "parse_us_date", "def parse_us_date(s):",
      "Convert a US-format date 'MM/DD/YYYY' to ISO 'YYYY-MM-DD' (zero-padded).",
      ["parse_us_date('01/01/2020') == '2020-01-01'", "len(parse_us_date('12/12/2012')) == 10"],
      ["parse_us_date('03/04/2021') == '2021-03-04'", "parse_us_date('12/31/1999') == '1999-12-31'",
       "parse_us_date('7/4/2021') == '2021-07-04'"],
      "from datetime import datetime\n\ndef parse_us_date(s):\n    return datetime.strptime(s, '%m/%d/%Y')"
      ".strftime('%Y-%m-%d')",
      ["def parse_us_date(s):\n    d, m, y = s.split('/')\n    return f'{y}-{int(m):02d}-{int(d):02d}'"], ["parsing"]),
    K("add_days", "dates", "add_days", "def add_days(d, n):",
      "Given an ISO date string 'YYYY-MM-DD' and an integer n, return the ISO date n days later.",
      ["add_days('2021-01-01', 1) == '2021-01-02'", "add_days('2021-01-10', 0) == '2021-01-10'"],
      ["add_days('2021-01-31', 1) == '2021-02-01'", "add_days('2020-02-28', 1) == '2020-02-29'",
       "add_days('2021-12-31', 1) == '2022-01-01'"],
      "from datetime import date, timedelta\n\ndef add_days(d, n):\n    return (date.fromisoformat(d) + "
      "timedelta(days=n)).isoformat()",
      ["def add_days(d, n):\n    y, m, dd = map(int, d.split('-'))\n    return f'{y:04d}-{m:02d}-{dd + n:02d}'"],
      ["arithmetic"]),
    K("weekday_name", "dates", "weekday_name", "def weekday_name(d):",
      "Return the English weekday name ('Monday'..'Sunday') of the ISO date string d.",
      ["weekday_name('2024-01-01') in ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday')",
       "isinstance(weekday_name('2020-02-02'), str)"],
      ["weekday_name('2024-01-01') == 'Monday'", "weekday_name('2021-07-04') == 'Sunday'",
       "weekday_name('2000-02-29') == 'Tuesday'"],
      "from datetime import date\n\ndef weekday_name(d):\n    return ['Monday', 'Tuesday', 'Wednesday', 'Thursday', "
      "'Friday', 'Saturday', 'Sunday'][date.fromisoformat(d).weekday()]",
      ["from datetime import date\n\ndef weekday_name(d):\n    return ['Monday', 'Tuesday', 'Wednesday', 'Thursday', "
       "'Friday', 'Saturday', 'Sunday'][date.fromisoformat(d).isoweekday() % 7]"], ["weekday"]),
    # ------------------------------------------------------------------ rounding
    K("round_half_up", "rounding", "round_half_up", "def round_half_up(x, nd):",
      "Round the number x to nd decimal places, rounding halves away from zero as taught in school "
      "(2.5 -> 3, 2.675 -> 2.68). Return a float.",
      ["round_half_up(1.24, 1) == 1.2", "round_half_up(3.0, 0) == 3.0"],
      ["round_half_up(2.5, 0) == 3.0", "round_half_up(2.675, 2) == 2.68", "round_half_up(-2.5, 0) == -3.0",
       "round_half_up(0.125, 2) == 0.13"],
      "from decimal import Decimal, ROUND_HALF_UP\n\ndef round_half_up(x, nd):\n    q = Decimal(1).scaleb(-nd)\n"
      "    return float(Decimal(str(x)).quantize(q, rounding=ROUND_HALF_UP))",
      ["def round_half_up(x, nd):\n    return round(x, nd)"], ["precision"]),
    K("money_total", "rounding", "money_total", "def money_total(prices):",
      "Given prices as strings like '0.10', return their exact total as a string with two decimals.",
      ["money_total(['1.00', '2.00']) == '3.00'", "money_total([]) == '0.00'"],
      ["money_total(['0.10', '0.20']) == '0.30'", "money_total(['0.10'] * 3) == '0.30'",
       "money_total(['1.005']) == '1.01'", "money_total(['2.675', '0']) == '2.68'"],
      "from decimal import Decimal, ROUND_HALF_UP\n\ndef money_total(prices):\n    t = sum((Decimal(p) for p in prices), "
      "Decimal('0'))\n    return str(t.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))",
      ["def money_total(prices):\n    return '%.2f' % sum(float(p) for p in prices)"], ["currency"]),
    K("percent", "rounding", "percent", "def percent(part, whole):",
      "Return part/whole as a percentage rounded half-up to one decimal place (a float).",
      ["percent(1, 2) == 50.0", "percent(0, 5) == 0.0"],
      ["percent(1, 8) == 12.5", "percent(1, 16) == 6.3", "percent(5, 16) == 31.3"],
      "from decimal import Decimal, ROUND_HALF_UP\n\ndef percent(part, whole):\n"
      "    v = Decimal(part) * 100 / Decimal(whole)\n    return float(v.quantize(Decimal('0.1'), rounding=ROUND_HALF_UP))",
      ["def percent(part, whole):\n    return round(part * 100 / whole, 1)"], ["ratio"]),
    K("split_bill", "rounding", "split_bill", "def split_bill(total_cents, n):",
      "Split an integer amount of cents among n people so the shares are as equal as possible and sum exactly to "
      "the total; larger shares first. Return a list of ints.",
      ["split_bill(100, 2) == [50, 50]", "sum(split_bill(90, 3)) == 90"],
      ["split_bill(100, 3) == [34, 33, 33]", "sum(split_bill(101, 4)) == 101", "split_bill(5, 3) == [2, 2, 1]"],
      "def split_bill(total_cents, n):\n    q, r = divmod(total_cents, n)\n    return [q + 1] * r + [q] * (n - r)",
      ["def split_bill(total_cents, n):\n    return [total_cents // n] * n"], ["division"]),
    K("avg_rounded", "rounding", "avg_rounded", "def avg_rounded(xs):",
      "Return the mean of xs rounded half-up to 2 decimal places as a float (empty list -> 0.0).",
      ["avg_rounded([1, 2, 3]) == 2.0", "avg_rounded([]) == 0.0"],
      ["avg_rounded([0.125]) == 0.13", "avg_rounded([1, 2]) == 1.5", "avg_rounded([2.675, 2.675]) == 2.68"],
      "from decimal import Decimal, ROUND_HALF_UP\n\ndef avg_rounded(xs):\n    if not xs:\n        return 0.0\n"
      "    m = sum(Decimal(str(x)) for x in xs) / len(xs)\n    return float(m.quantize(Decimal('0.01'), "
      "rounding=ROUND_HALF_UP))",
      ["def avg_rounded(xs):\n    return round(sum(xs) / len(xs), 2) if xs else 0.0"], ["mean"]),
    # ------------------------------------------------------------------ retry
    K("backoff_delays", "retry", "backoff_delays", "def backoff_delays(base, factor, n, cap):",
      "Return the list of n retry delays base * factor**i (i = 0..n-1), each capped at `cap`.",
      ["backoff_delays(1, 2, 3, 100) == [1, 2, 4]", "backoff_delays(1, 2, 0, 5) == []"],
      ["backoff_delays(1, 2, 5, 5) == [1, 2, 4, 5, 5]", "backoff_delays(3, 3, 3, 10) == [3, 9, 10]"],
      "def backoff_delays(base, factor, n, cap):\n    return [min(cap, base * factor ** i) for i in range(n)]",
      ["def backoff_delays(base, factor, n, cap):\n    return [base * factor ** i for i in range(n)]"], ["schedule"]),
    K("attempts_used", "retry", "attempts_used", "def attempts_used(success_at, max_tries):",
      "An operation succeeds on attempt number success_at (1-based; None = never). Return how many attempts are "
      "made with at most max_tries attempts.",
      ["attempts_used(1, 3) == 1", "attempts_used(None, 3) == 3"],
      ["attempts_used(3, 3) == 3", "attempts_used(5, 3) == 3", "attempts_used(2, 5) == 2"],
      "def attempts_used(success_at, max_tries):\n    return max_tries if success_at is None else "
      "min(success_at, max_tries)",
      ["def attempts_used(success_at, max_tries):\n    return max_tries if success_at is None else success_at"],
      ["attempts"]),
    K("total_wait", "retry", "total_wait", "def total_wait(base, n, cap):",
      "Return the total waiting time of n retries with delays base * 2**i (i = 0..n-1), each delay capped at cap.",
      ["total_wait(1, 3, 100) == 7", "total_wait(1, 0, 10) == 0"],
      ["total_wait(1, 5, 4) == 15", "total_wait(2, 4, 5) == 16"],
      "def total_wait(base, n, cap):\n    return sum(min(cap, base * 2 ** i) for i in range(n))",
      ["def total_wait(base, n, cap):\n    return sum(base * 2 ** i for i in range(n))"], ["duration"]),
    K("should_retry", "retry", "should_retry", "def should_retry(status):",
      "Return True if an HTTP response with this status code should be retried: 429 and 5xx server errors are "
      "retryable except 501 (not implemented); everything else is not.",
      ["should_retry(503)", "not should_retry(200)"],
      ["should_retry(429)", "not should_retry(404)", "not should_retry(501)", "should_retry(500)", "not should_retry(400)"],
      "def should_retry(status):\n    return status == 429 or (500 <= status <= 599 and status != 501)",
      ["def should_retry(status):\n    return status >= 400"], ["http"]),
    K("jitter_bounds", "retry", "jitter_bounds", "def jitter_bounds(delay, frac):",
      "Return (low, high) = (delay*(1-frac), delay*(1+frac)) as floats, with low clamped to be at least 0.",
      ["jitter_bounds(10, 0.5) == (5.0, 15.0)", "jitter_bounds(0, 0.5) == (0.0, 0.0)"],
      ["jitter_bounds(10, 1.5) == (0.0, 25.0)", "jitter_bounds(4, 2) == (0.0, 12.0)"],
      "def jitter_bounds(delay, frac):\n    return (max(0.0, float(delay * (1 - frac))), float(delay * (1 + frac)))",
      ["def jitter_bounds(delay, frac):\n    return (float(delay * (1 - frac)), float(delay * (1 + frac)))"], ["jitter"]),
]

KATA_BY_ID = {k.id: k for k in KATAS}
CLASSES = tuple(CLASS_KEYWORDS)
