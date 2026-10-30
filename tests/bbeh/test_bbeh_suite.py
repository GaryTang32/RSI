"""BBEH suites: cost-driven sizes, pilot 5/family, disjoint splits, unseen OOD families, families.json."""
from __future__ import annotations

from pathlib import Path

import pytest

from rsi.domains.bbeh import (DEFAULT_OOD, DEFAULT_PRACTICE, TASKS, load_families, make_pilot_suite, make_suite,
                              suggest_families)

ROOT = Path(__file__).resolve().parents[2]


def test_default_sizes(bkw):
    s = make_suite(**bkw)
    sm = s.summary()
    assert (sm["evolve"]["n"], sm["holdout"]["n"], sm["ood"]["n"]) == (24, 36, 36) and s.name == "bbeh-s0"
    assert sm["evolve"]["families"] == sorted(DEFAULT_PRACTICE) and sm["ood"]["families"] == sorted(DEFAULT_OOD)
    assert not set(s.families("evolve")) & set(s.families("ood"))
    p = make_pilot_suite(**bkw)
    assert len(p.splits["pilot"]) == 5 * 23 and p.families() == sorted(TASKS)


@pytest.mark.parametrize("seed", [0, 2])
def test_no_example_in_two_splits_including_the_pilot(bkw, seed):
    s, p = make_suite(seed=seed, **bkw), make_pilot_suite(**bkw)
    seen = set()
    for suite, splits in ((s, ("evolve", "holdout", "ood")), (p, ("pilot",))):
        for split in splits:
            for t in suite.split(split, allow_sealed=True):
                key = (t.family, t.meta["bbh_index"])
                assert key not in seen
                seen.add(key)


def test_deterministic_and_seeded(bkw):
    a, b, c = make_suite(seed=0, **bkw), make_suite(seed=0, **bkw), make_suite(seed=1, **bkw)
    assert a.splits == b.splits and a.splits["evolve"] != c.splits["evolve"]


def test_unknown_or_bbh_only_families_are_rejected(bkw):
    with pytest.raises(ValueError, match="unknown BBEH tasks"):
        make_suite(["navigate"], ["word_sorting"], **bkw)
    with pytest.raises(ValueError, match="remain after the pilot reserve"):
        make_suite(per_family={"evolve": 20}, **bkw)


def test_families_json_is_valid_and_matches_the_defaults_initially():
    fam = load_families(ROOT / "experiments" / "bbeh" / "families.json")
    assert (tuple(fam["practice"]), tuple(fam["ood"])) == (DEFAULT_PRACTICE, DEFAULT_OOD)
    assert sorted(fam["pilot_candidates"]) == sorted(TASKS)


def test_suggest_uses_the_bbeh_band():
    sug = suggest_families({"a": 0.5, "b": 0.8, "c": 0.7, "d": 0.1}, n_practice=1, n_ood=1)
    assert set(sug["excluded_floor_or_ceiling"]) == {"b", "d"} and set(sug["practice"] + sug["ood"]) == {"a", "c"}

