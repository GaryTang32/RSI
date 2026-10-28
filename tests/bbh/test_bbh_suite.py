"""BBH suites: sizes, determinism, disjoint splits (pilot included), unseen OOD families, families.json."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from rsi.core import SealedSplitError
from rsi.domains.bbh import (DEFAULT_OOD, DEFAULT_PRACTICE, PILOT_CANDIDATES, load_families, make_pilot_suite,
                             make_suite, suggest_families)
from rsi.domains.bbh.suite import sibling_group

ROOT = Path(__file__).resolve().parents[2]


def _ids(suite, split):
    return [t.id for t in suite.split(split, allow_sealed=True)]


def test_default_sizes_and_families(kw):
    s = make_suite(**kw)
    sm = s.summary()
    assert (sm["evolve"]["n"], sm["holdout"]["n"], sm["ood"]["n"]) == (36, 60, 60)
    assert sm["evolve"]["families"] == sorted(DEFAULT_PRACTICE) == sm["holdout"]["families"]
    assert sm["ood"]["families"] == sorted(DEFAULT_OOD)
    assert "val" not in s.splits and s.splits["smoke"] == s.splits["evolve"][:2]
    with pytest.raises(SealedSplitError):
        s.split("holdout")
    with pytest.raises(SealedSplitError):
        s.split("ood")
    s.split("evolve")


def test_per_family_counts_are_configurable(kw):
    s = make_suite(DEFAULT_PRACTICE[:2], DEFAULT_OOD[:3], per_family={"evolve": 4, "val": 2, "holdout": 3, "ood": 3},
                   **kw)
    assert [len(s.splits[x]) for x in ("evolve", "val", "holdout", "ood")] == [8, 4, 6, 9]
    with pytest.raises(ValueError, match="remain after the pilot reserve"):
        make_suite(per_family={"evolve": 20}, **kw)
    with pytest.raises(ValueError, match="unknown per_family"):
        make_suite(per_family={"train": 1}, **kw)
    with pytest.raises(ValueError, match="both practice and ood"):
        make_suite(["navigate"], ["navigate"], **kw)
    with pytest.raises(ValueError, match="unknown BBH tasks"):
        make_suite(["nope"], ["navigate"], **kw)


def test_deterministic_and_seeded(kw):
    a, b, c = make_suite(seed=0, **kw), make_suite(seed=0, **kw), make_suite(seed=1, **kw)
    for split in ("evolve", "holdout", "ood"):
        assert _ids(a, split) == _ids(b, split)
        assert [t.input for t in a.split(split, allow_sealed=True)] == [t.input for t in b.split(split, allow_sealed=True)]
    assert _ids(a, "evolve") != _ids(c, "evolve")


@pytest.mark.parametrize("seed", [0, 1, 7])
def test_no_example_in_two_splits_including_the_pilot(kw, seed):
    s = make_suite(seed=seed, per_family={"val": 2}, **kw)
    p = make_pilot_suite(**kw)
    seen: dict = {}
    for suite, splits in ((s, ("evolve", "val", "holdout", "ood")), (p, ("pilot",))):
        for split in splits:
            for t in suite.split(split, allow_sealed=True):
                key = (t.family, t.meta["bbh_index"])
                assert key not in seen, f"{key} in {seen.get(key)} and {split}"
                assert t.input not in {x for x, _ in seen.values()}
                seen[key] = (t.input, split)
    assert len(p.splits["pilot"]) == 10 * len(PILOT_CANDIDATES)


def test_pilot_pool_is_fixed_and_independent_of_the_suite_seed(kw):
    a = make_pilot_suite(["navigate", "word_sorting"], **kw)
    b = make_pilot_suite(["word_sorting", "navigate", "snarks"], **kw)
    assert set(a.splits["pilot"]) <= set(b.splits["pilot"])
    for seed in (0, 3):
        s = make_suite(["word_sorting"], ["navigate"], seed=seed, **kw)
        used = {(t.family, t.meta["bbh_index"]) for split in ("evolve", "holdout", "ood")
                for t in s.split(split, allow_sealed=True)}
        assert not used & {(t.family, t.meta["bbh_index"]) for t in a.split("pilot")}


def test_ood_families_never_appear_in_evolve(kw):
    s = make_suite(**kw)
    assert not set(s.families("evolve")) & set(s.families("ood"))
    assert not {sibling_group(f) for f in s.families("evolve")} & {sibling_group(f) for f in s.families("ood")}


def test_families_json_is_valid_and_matches_the_defaults_initially():
    fam = load_families(ROOT / "experiments" / "bbh" / "families.json")
    assert len(fam["practice"]) == 6 and len(fam["ood"]) == 6 and not set(fam["practice"]) & set(fam["ood"])
    assert set(fam["practice"]) | set(fam["ood"]) <= set(fam["pilot_candidates"])
    assert (tuple(fam["practice"]), tuple(fam["ood"])) == (DEFAULT_PRACTICE, DEFAULT_OOD)


def test_families_file_drives_make_suite(kw, tmp_path):
    p = tmp_path / "f.json"
    p.write_text(json.dumps({"practice": ["snarks", "hyperbaton"], "ood": ["navigate"]}))
    s = make_suite(families_file=p, per_family={"evolve": 1, "holdout": 1, "ood": 1}, **kw)
    assert s.families("evolve") == ["hyperbaton", "snarks"] and s.families("ood") == ["navigate"]


def test_suggest_families_drops_ceiling_and_keeps_siblings_together():
    acc = {"a": 0.5, "b": 0.45, "c": 0.95, "d": 0.05, "logical_deduction_three_objects": 0.55,
           "logical_deduction_five_objects": 0.5, "e": 0.6, "f": 0.4}
    sug = suggest_families(acc, n_practice=2, n_ood=2)
    assert {"c", "d"} == set(sug["excluded_floor_or_ceiling"])
    picked = sug["practice"] + sug["ood"]
    assert len(picked) == 4 and not {"c", "d"} & set(picked)
    assert sum(f.startswith("logical_deduction") for f in picked) <= 1
