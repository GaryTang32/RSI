"""BIG-Bench Extra Hard (BBEH) harness-evolution domain, built on the BBH machinery with BBEH's official grader."""
from functools import partial as _partial

from ..bbh import suite as _suite
from ..bbh.leak import leak_check as _leak_check
from .data import BBEH, TASKS, BBHChecksumError, BBHDataError, default_cache_dir, ensure_all, fetch_file, load_examples, load_manifest
from .domain import BBEHDomain, BBEHSimModel
from .grading import evaluate_correctness, extract_answer, fuzzy_match, is_correct, is_generic_target

DEFAULT_PRACTICE = BBEH.default_practice
DEFAULT_OOD = BBEH.default_ood
DEFAULT_PER_FAMILY = dict(BBEH.default_per_family)
PILOT_PER_FAMILY = BBEH.pilot_per_family
make_suite = _partial(_suite.make_suite, benchmark=BBEH)
make_pilot_suite = _partial(_suite.make_pilot_suite, benchmark=BBEH)
load_families = _partial(_suite.load_families, benchmark=BBEH)
leak_check = _partial(_leak_check, is_generic=is_generic_target)


def suggest_families(acc, **kw):
    """BBH's rule with BBEH's accuracy band [0.15, 0.75]."""
    lo, hi = BBEH.pilot_band
    return _suite.suggest_families(acc, **{"lo": lo, "hi": hi, **kw})


__all__ = ["BBEH", "TASKS", "BBHChecksumError", "BBHDataError", "default_cache_dir", "ensure_all", "fetch_file",
           "load_examples", "load_manifest", "BBEHDomain", "BBEHSimModel", "evaluate_correctness", "extract_answer",
           "fuzzy_match", "is_correct", "is_generic_target", "DEFAULT_PRACTICE", "DEFAULT_OOD", "DEFAULT_PER_FAMILY",
           "PILOT_PER_FAMILY", "make_suite", "make_pilot_suite", "load_families", "leak_check", "suggest_families"]
