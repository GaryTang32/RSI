"""A new-seed re-score or leak check must draw trial seeds no other evaluation of the run used."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "experiments" / "demo"))

from live_demo import new_seed_problem  # noqa: E402


def test_seeds_the_loops_monitors_or_recorded_rescore_used_are_refused(tmp_path):
    for offset in (-1, 0, 1, 99, 100, 101):     # k = 2: 99 reaches 100, 101 is the recorded re-score's
        assert new_seed_problem(tmp_path, f"rescore_s{offset}.json", offset), offset
        assert new_seed_problem(tmp_path, f"leak_ablation_s{offset}.json", offset), offset
    assert new_seed_problem(tmp_path, "rescore_s102.json", 102) is None


def test_overlap_with_an_existing_record_is_refused_except_a_paired_offset(tmp_path):
    (tmp_path / "rescore_s200.json").write_text(json.dumps({"seed_offset": 200, "k": 2}))
    (tmp_path / "rescore_s200_check.json").write_text("{}")
    assert new_seed_problem(tmp_path, "rescore_s200.json", 200) is None          # checking the record itself
    assert new_seed_problem(tmp_path, "leak_ablation_s200.json", 200) is None    # paired, as 100 is
    for offset in (199, 201):
        assert "rescore_s200.json" in new_seed_problem(tmp_path, f"rescore_s{offset}.json", offset)
        assert "rescore_s200.json" in new_seed_problem(tmp_path, f"leak_ablation_s{offset}.json", offset)
    assert new_seed_problem(tmp_path, "rescore_s202.json", 202) is None
