"""BBEH grading: our ``ANSWER:`` extraction, then the official BBEH evaluation pipeline.

1. Our extraction: the text after the last ``ANSWER:`` (any case, bold allowed, as on AgentQA/BBH) if present, else the
   whole output.
2. The official pipeline of ``bbeh/evaluate.py`` (google-deepmind/bbeh at commit 80d12ca9): ``extract_answer``
   (split on "The answer is:", "The final answer is ", "The final answer is: ", "The answer is "; drop one trailing
   "."; ``strip_latex``), ``preprocess_sample`` (lower-case, ", " -> ",", drop "**", first line, trailing "."),
   ``preprocess_reference`` (strip, lower-case, ", " -> ",") and ``fuzzy_match`` ((a) vs a, float equality, quotes,
   [x] brackets, a trailing "?").

The functions below marked "port" are a line-by-line Python port of that file, which is
Copyright 2025 Google LLC and licensed under the Apache License, Version 2.0
(http://www.apache.org/licenses/LICENSE-2.0); it was read as text and re-typed here, never imported or executed.
"""
from __future__ import annotations

import re

_ANSWER_LINE = re.compile(r"ANSWER\s*\**\s*[:=]\s*\**\s*(.+)", re.I)
OFFICIAL_PREFIXES = ("The answer is:", "The final answer is ", "The final answer is: ", "The answer is ")


_WRAPPED = re.compile(r"(\*\*)?\s*<([^<>]+)>\s*(\*\*)?")


def answer_text(output: str) -> str:
    """Our step: the text after the last ``ANSWER:`` if any, else the whole output.

    An answer wrapped whole in ``<...>`` is unwrapped: the seed prompt's own placeholder (``ANSWER: <answer>``) invites
    it, and no BBEH target is so wrapped."""
    text = str(output or "")
    m = _ANSWER_LINE.findall(text)
    if not m:
        return text
    ans = m[-1].strip()
    w = _WRAPPED.fullmatch(ans.split("\n")[0].strip())
    return w.group(2).strip() if w else ans


# ----- port of bbeh/evaluate.py (Apache 2.0, Google LLC) -----------------------------------------------------------

def strip_latex(response: str) -> str:  # port
    if response.startswith("$") and response.endswith("$"):
        response = response[1:-1]
    if "boxed{" in response and response.endswith("}"):
        response = response[0:-1].split("boxed{")[1]
    if "text{" in response and response.endswith("}"):
        response = response[0:-1].split("text{")[1]
    if "texttt{" in response and response.endswith("}"):
        response = response[0:-1].split("texttt{")[1]
    return response


def official_extract_answer(sample: str) -> str:  # port of extract_answer
    answer = sample
    for answer_prefix in OFFICIAL_PREFIXES:
        if answer_prefix in answer:
            answer = answer.split(answer_prefix)[-1].strip()
    if answer.endswith("."):
        answer = answer[:-1]
    return strip_latex(answer)


def fuzzy_match(prediction: str, reference: str) -> bool:  # port
    if prediction == reference:
        return True
    # (a) vs a
    if len(prediction) == 3 and prediction[0] == "(" and prediction[-1] == ")":
        return prediction[1] == reference
    if len(reference) == 3 and reference[0] == "(" and reference[-1] == ")":
        return reference[1] == prediction
    # numbers
    try:
        if float(prediction) == float(reference):
            return True
    except ValueError:
        pass
    # quote issues
    if prediction.replace("'", "") == reference.replace("'", ""):
        return True
    # bracket issues
    if f"[{reference}]" == prediction or f"[{prediction}]" == reference:
        return True
    # question mark issues
    if prediction.endswith("?") and prediction[:-1] == reference:
        return True
    return False


def preprocess_sample(sample: str) -> str:  # port
    prediction = official_extract_answer(sample.strip()).lower()
    prediction = prediction.replace(", ", ",").replace("**", "")
    prediction = prediction.split("\n")[0]
    prediction = prediction[0:-1] if prediction.endswith(".") else prediction
    return prediction


def preprocess_reference(reference: str) -> str:  # port
    reference = reference.strip().lower()
    reference = reference.replace(", ", ",")
    return reference


def evaluate_correctness(sample: str, reference: str) -> bool:  # port
    return fuzzy_match(preprocess_sample(sample), preprocess_reference(reference))

# ----- end of port -------------------------------------------------------------------------------------------------


def is_correct(target: str, output: str, question=None) -> bool:
    """Our extraction, then the official pipeline (``question`` is unused; kept for the BBH signature).

    One addition to the official rule: a reference ending in "." (11 linguini targets, e.g. "He really cried.") is
    also compared without that period. The official pipeline drops the prediction's trailing period but keeps the
    reference's, so it can never credit those targets."""
    pred, ref = preprocess_sample(answer_text(output)), preprocess_reference(str(target))
    return fuzzy_match(pred, ref) or (ref.endswith(".") and fuzzy_match(pred, ref[:-1]))


def extract_answer(output: str) -> str:
    """The answer as the official pipeline sees it, before lower-casing (for feedback)."""
    return official_extract_answer(answer_text(output).strip()).replace("**", "").split("\n")[0].strip()


def is_generic_target(target: str) -> bool:
    """For the leak check: targets of at most 2 whitespace tokens (letters, numbers, yes/no, short pairs)."""
    return len(str(target).split()) <= 2
