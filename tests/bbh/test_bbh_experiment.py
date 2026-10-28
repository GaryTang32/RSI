"""experiments/bbh: every arm runs offline with the mock LLMs, caps stop arms, merge builds the paired table."""
from __future__ import annotations

import importlib
import json
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SMALL = ["--evolve", "2", "--holdout", "2", "--ood", "2", "--n-sub", "4", "--rrsi-T", "2", "--gepa-B", "30",
         "--mh-iterations", "1", "--workers", "2"]


@pytest.fixture
def exp(monkeypatch, bbh_offline):
    monkeypatch.syspath_prepend(str(ROOT / "experiments" / "bbh"))
    return importlib.import_module("compare_live"), importlib.import_module("merge")


def test_offline_dry_run_of_every_arm_then_merge(exp, tmp_path, capsys):
    cl, merge = exp
    assert cl.main(["--arm", "all", "--offline", "--out-dir", str(tmp_path), *SMALL]) == 0
    res = {a: json.loads((tmp_path / f"{a}.json").read_text()) for a in cl.ARMS}
    assert all(r["status"] == "ok" for r in res.values()), {a: (r["status"], r.get("error")) for a, r in res.items()}
    for a in ("seed", "cot3", "rrsi", "gepa", "metaharness"):
        assert len(res[a]["per_task"]) == 24                 # 6 + 6 families x 2 examples
        assert {p["phase"] for p in res[a]["phases"]} >= {"eval_holdout", "eval_ood"}
    for a in cl.SEARCHED:
        assert res[a]["leak_check"]["passed"] and res[a]["search"] and Path(res[a]["final_harness"]["dir"]).is_dir()
    assert res["gepa"]["search"]["noise_margin"] > 0
    for a in ("harness2", "bestofn"):
        assert len(res[a]["per_task"]) == 4 and res[a]["subset"] == res["harness2"]["subset"]
    assert res["bestofn"]["n"] == round(res["harness2"]["calls_per_task"] - 1)
    assert set(res["pilot"]["family_accuracy"]) == set(json.loads(
        (ROOT / "experiments" / "bbh" / "families.json").read_text())["pilot_candidates"])
    assert merge.main(["--dir", str(tmp_path), "--reps", "500"]) == 0
    m = json.loads((tmp_path / "merged.json").read_text())
    assert set(m["arms"]) == set(cl.ARMS) - {"pilot"} and not m["mismatched_suites"]
    assert m["arms"]["rrsi"]["vs_seed"]["n"] == 24 and m["arms"]["harness2"]["vs_seed"]["n"] == 4
    assert "harness2_minus_bestofn" in m["paired_extra"] and "| seed |" in capsys.readouterr().out


def test_a_cap_stops_an_arm_between_phases(exp, tmp_path):
    cl, _ = exp
    o = cl.run_arm(cl.parse_args(["--arm", "seed", "--offline", "--cap", "0", "--out-dir", str(tmp_path), *SMALL]),
                   "seed")
    assert o["status"] == "cap_reached" and not o.get("per_task") and "before eval holdout" in o["stop"]
    assert json.loads((tmp_path / "seed.json").read_text())["status"] == "cap_reached"


def test_subsample_is_half_holdout_half_ood_family_stratified_and_fixed(exp, kw):
    cl, _ = exp
    from rsi.domains.bbh import make_suite
    s = make_suite(**kw)
    a, b = cl.subsample(s, 36, 0), cl.subsample(s, 36, 0)
    assert [t.id for t in a] == [t.id for t in b] and len({t.id for t in a}) == 36
    assert Counter(t.id.split("-")[0] for t in a) == {"holdout": 18, "ood": 18}
    assert set(Counter(t.family for t in a).values()) == {3}


def test_smoke_flag_is_two_tasks(exp, tmp_path):
    cl, _ = exp
    a = cl.parse_args(["--arm", "seed", "--smoke", "--offline", "--out-dir", str(tmp_path)])
    _, suite = cl.build_suite(a)
    assert len(suite.splits["holdout"]) + len(suite.splits["ood"]) == 2 and a.k == 1 and a.n_sub == 2
