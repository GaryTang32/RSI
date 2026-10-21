"""A new-seed re-score or leak check must draw trial seeds no other evaluation of the run used."""
import itertools
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "experiments" / "demo"))

from live_demo import new_seed_problem, traces_identical  # noqa: E402

KINDS = ("rescore", "leak_ablation")


def _problem(run: Path, offset: int, kind: str = "rescore"):
    return new_seed_problem(run, f"{kind}_s{offset}.json", offset)


def test_seeds_the_loops_monitors_or_recorded_rescore_used_are_refused(tmp_path):
    for offset, kind in itertools.product((-1, 0, 1, 99, 100, 101), KINDS):   # k = 2: 99 reaches 100
        assert _problem(tmp_path, offset, kind), (offset, kind)
    assert _problem(tmp_path, 102) is None


def test_overlap_with_an_existing_record_is_refused_except_a_paired_offset(tmp_path):
    (tmp_path / "rescore_s200.json").write_text(json.dumps({"seed_offset": 200, "k": 2}))
    (tmp_path / "rescore_s200_check.json").write_text("{}")
    assert _problem(tmp_path, 200) is None                      # checking the record itself
    assert _problem(tmp_path, 200, "leak_ablation") is None     # paired, as 100 is
    for offset, kind in itertools.product((199, 201), KINDS):
        assert "rescore_s200.json" in _problem(tmp_path, offset, kind)
    assert _problem(tmp_path, 202) is None


def test_seeds_an_rrsi_loop_recorded_are_refused(tmp_path):
    (tmp_path / "evals").mkdir()
    (tmp_path / "evals" / "base.json").write_text(json.dumps({"job": "base", "seeds": [5000]}))
    (tmp_path / "r0" / "A").mkdir(parents=True)
    (tmp_path / "r0" / "A" / "eval.json").write_text(json.dumps({"summary": {"seeds": [7000, 7001]}}))
    for offset in (4999, 5000, 6999, 7000, 7001):
        assert "used by an evaluation of the run" in _problem(tmp_path, offset)
    for offset in (5001, 6998, 7002):
        assert _problem(tmp_path, offset) is None


def test_a_replay_with_traces_the_run_lost_is_flagged(tmp_path):
    for d, names in ((tmp_path / "run", ("t1", "t2")), (tmp_path / "replay", ("t1", "t2", "t3"))):
        traces = d / "store" / "candidates" / "c1" / "eval" / "search" / "traces"
        traces.mkdir(parents=True)
        for n in names:
            (traces / n).write_text(n)
    assert traces_identical(tmp_path / "run", tmp_path / "replay") == {
        "files": 2, "identical": 2, "missing_in_replay": 0, "extra_in_replay": 1}
