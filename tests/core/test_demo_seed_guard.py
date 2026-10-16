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


def test_seeds_an_rrsi_loop_recorded_are_refused(tmp_path):
    (tmp_path / "evals").mkdir()
    (tmp_path / "evals" / "base.json").write_text(json.dumps({"job": "base", "seeds": [5000]}))
    (tmp_path / "r0" / "A").mkdir(parents=True)
    (tmp_path / "r0" / "A" / "eval.json").write_text(json.dumps({"summary": {"seeds": [7000, 7001]}}))
    for offset in (4999, 5000, 6999, 7000, 7001):
        assert "used by an evaluation of the run" in new_seed_problem(tmp_path, f"rescore_s{offset}.json", offset)
    for offset in (5001, 6998, 7002):
        assert new_seed_problem(tmp_path, f"rescore_s{offset}.json", offset) is None


def test_a_replay_with_traces_the_run_lost_is_flagged(tmp_path):
    from live_demo import traces_identical
    for d, names in ((tmp_path / "run", ("t1", "t2")), (tmp_path / "replay", ("t1", "t2", "t3"))):
        tr = d / "store" / "candidates" / "c1" / "eval" / "search" / "traces"
        tr.mkdir(parents=True)
        for n in names:
            (tr / n).write_text(n)
    assert traces_identical(tmp_path / "run", tmp_path / "replay") == {
        "files": 2, "identical": 2, "missing_in_replay": 0, "extra_in_replay": 1}
