"""Hand-written experience per kata class, in three representations (for X1-X3).

For each class, one fail->pass experience is written up as

* ``CLASS_GENES[cls]`` - a compact Gene (keywords / summary / 4 steps / 2 AVOID
  items, ~150-230 tokens);
* ``render_skill(CLASS_GENES[cls])`` - the long SKILL.md form (~2,000+ tokens) with
  the same content embedded in documentation;
* ``FAILURE_LOGS[cls]`` - the raw failure history ("naively appended"),
* ``COMPLEMENTARY_GENES[cls]`` - a second, *different but also correct* gene;
* ``CONFLICTING_GENES[cls]`` - a gene whose advice contradicts the correct one.
"""
from __future__ import annotations

from rsi.evomap.assets import Gene

V = ["python smoke_test.py"]


def G(cls, gid, summary, strategy, avoid, extra_signals=()):
    from .katas import CLASS_KEYWORDS
    return Gene(id=gid, category="repair", signals_match=[*CLASS_KEYWORDS[cls], *extra_signals], summary=summary,
                strategy=list(strategy), avoid=list(avoid), validation=list(V),
                provenance={"kind": "manual", "source": "katas.genes"})


CLASS_GENES = {
    "boundaries": G("boundaries", "gene_inclusive_boundaries",
                    "Boundary / off-by-one bugs: decide inclusivity explicitly and test the edges.",
                    ["Restate every bound from the spec as inclusive or exclusive before coding.",
                     "For an inclusive upper bound use <= and range(a, b + 1).",
                     "Count partial groups (pages, chunks) with ceiling division -(-a // b).",
                     "Test the edges: empty input, n == 0, one element, exact multiples, one past the end."],
                    ["lst[-n:] when n can be 0 (it returns the whole list).",
                     "Floor division when a partial group still counts."]),
    "unicode": G("unicode", "gene_unicode_normalize_casefold",
                 "Compare, count and measure text only after Unicode normalization and casefolding.",
                 ["Normalize every string with unicodedata.normalize('NFC', s) before comparing, counting or measuring.",
                  "Use str.casefold(), not lower(), for case-insensitive comparison (German sharp s).",
                  "To strip accents: normalize('NFD'), drop unicodedata.combining(c) marks, re-normalize NFC.",
                  "Count visible characters by skipping combining marks after NFC."],
                 ["encode('ascii', 'ignore'): it deletes letters, not just accents.",
                  "Comparing raw strings: an accented letter can be one or two code points."]),
    "dates": G("dates", "gene_datetime_not_manual_math",
               "Date bugs: parse with explicit formats and do arithmetic with datetime, never by hand.",
               ["Parse with date.fromisoformat or datetime.strptime and an explicit format such as '%m/%d/%Y'.",
                "Do date arithmetic with datetime.date and timedelta, never with hand-rolled day counts.",
                "Gregorian leap year: divisible by 4 and (not by 100, or by 400).",
                "Index weekday names with date.weekday() where Monday == 0."],
               ["Assuming 365-day years or 30-day months.",
                "Reading US dates as day/month: US order is month/day/year."]),
    "rounding": G("rounding", "gene_decimal_half_up",
                  "Rounding and money: use Decimal with ROUND_HALF_UP; never round binary floats.",
                  ["Convert inputs with Decimal(str(x)) or Decimal(text) before any arithmetic.",
                   "Round with quantize(Decimal('0.01'), rounding=ROUND_HALF_UP) for school rounding.",
                   "Split totals with divmod and give the remainder to the first shares.",
                   "Format money from the Decimal result, not from a float."],
                  ["Python round(): banker's rounding on binary floats (2.675 -> 2.67).",
                   "'%.2f' formatting of float sums."]),
    "retry": G("retry", "gene_capped_backoff",
               "Retry logic: cap every delay, count attempts exactly, retry only transient errors.",
               ["Cap every computed delay: min(cap, base * factor ** i).",
                "Count attempts as min(success_at, max_tries); None means all max_tries attempts.",
                "Retry only on 429 and 5xx server errors, and never on 501.",
                "Clamp lower jitter bounds at zero with max(0.0, ...)."],
               ["Uncapped exponential growth of delays.", "Retrying on every status >= 400."]),
}

COMPLEMENTARY_GENES = {
    "boundaries": G("boundaries", "gene_boundary_examples_first",
                    "Work out small boundary examples by hand before writing the loop.",
                    ["Write down the expected output for the smallest inputs, including the last partial group.",
                     "Prefer slicing with explicit start indices computed with max(0, ...).",
                     "Compare against the examples before returning."],
                    ["Trusting a single happy-path example."]),
    "unicode": G("unicode", "gene_unicode_combining_marks",
                 "Treat combining marks as part of the preceding character.",
                 ["Inspect unicodedata.combining for each code point.",
                  "Build comparison keys from the normalized, casefolded text.",
                  "Re-check with precomposed and decomposed inputs."],
                 ["Counting code points as characters."]),
    "dates": G("dates", "gene_calendar_library_checks",
               "Lean on the standard calendar library and check month ends.",
               ["Use the calendar and datetime modules for month lengths and leap years.",
                "Check month-end and year-end transitions explicitly.",
                "Keep ISO 'YYYY-MM-DD' strings as the canonical format."],
               ["Adding days to the day field directly."]),
    "rounding": G("rounding", "gene_integer_cents",
                  "Represent money as exact decimals or integer cents.",
                  ["Keep amounts in integer cents or Decimal throughout.",
                   "Distribute remainders deterministically, largest shares first.",
                   "Only format at the very end."],
                  ["Accumulating float error in sums."]),
    "retry": G("retry", "gene_retry_policy_table",
               "Write the retry policy as an explicit table of status codes and limits.",
               ["List retryable statuses explicitly (429 and 500-599 except 501).",
                "Bound both the number of attempts and each delay.",
                "Keep jitter ranges non-negative."],
               ["Implicit catch-all retry conditions."]),
}

CONFLICTING_GENES = {
    "boundaries": G("boundaries", "gene_exclusive_bounds",
                    "Python ranges and bounds are exclusive at the top, so keep them that way.",
                    ["Use range(a, b) and < b for upper bounds everywhere.",
                     "Use floor division // for group counts.", "Slice with lst[-n:] for the last n items."],
                    ["Adding + 1 to bounds."]),
    "unicode": G("unicode", "gene_ascii_fold",
                 "Simplify text by folding to ASCII.",
                 ["Lower-case strings with lower().", "Drop non-ASCII characters with encode('ascii', 'ignore').",
                  "Compare the raw strings afterwards."], ["Importing unicodedata (slow)."]),
    "dates": G("dates", "gene_manual_date_math",
               "Avoid heavy datetime imports; compute dates with integer arithmetic.",
               ["Treat years as 365 days and months as 30 days.", "Split date strings and add days to the day field.",
                "Every fourth year is a leap year."], ["Using the datetime module."]),
    "rounding": G("rounding", "gene_builtin_round",
                  "Python's built-in round() is exact enough for money.",
                  ["Use round(x, 2) for all rounding.", "Sum floats directly and format with '%.2f'.",
                   "Split bills by floor division."], ["The decimal module (overkill)."]),
    "retry": G("retry", "gene_retry_everything",
               "Be generous with retries.",
               ["Retry on every status >= 400.", "Let delays grow without a cap so the server recovers.",
                "Allow negative jitter bounds."], ["Capping delays."]),
}

FAILURE_LOGS = {
    cls: ("attempt 1: FAILED hidden checks. Traceback (most recent call last):\n  File \"solution.py\", line 3, in "
          "<module>\nAssertionError\nattempt 1 notes: the implementation passed the two visible examples, so the "
          "failure was surprising. Re-read the task text; looked at the function again; tried a few inputs by hand; "
          "the output looked plausible. attempt 2: changed variable names and added a docstring, still FAILED "
          "hidden checks. attempt 3: after comparing with the reference behaviour the root cause was: "
          + "; ".join(CLASS_GENES[cls].avoid).lower() + ". Fixed version passed.\n") * 2
    for cls in CLASS_GENES
}
