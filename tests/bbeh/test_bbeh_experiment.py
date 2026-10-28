"""experiments/bbeh: every arm runs offline with the mock LLMs; cot3 is refused; merge builds the table."""
from __future__ import annotations

import importlib.util
import json
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SMALL = ["--evolve", "2", "--holdout", "2", "--ood", "2", "--n-sub", "4", "--rrsi-T", "1", "--gepa-B", "20",
         "--mh-iterations", "1", "--workers", "2"]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def exp(bbeh_offline):
    return _load("bbeh_cl_test", "experiments/bbeh/compare_live.py"), _load("bbeh_merge_test", "experiments/bbeh/merge.py")


def test_offline_dry_run_of_every_arm_then_merge(exp, tmp_path):
    cl, merge = exp
    assert "cot3" not in cl.ARMS and len(cl.ARMS) == 7
    assert cl.main(["--arm", "all", "--offline", "--out-dir", str(tmp_path), *SMALL]) == 0
    res = {a: json.loads((tmp_path / f"{a}.json").read_text()) for a in cl.ARMS}
    assert all(r["status"] == "ok" and r["benchmark"] == "bbeh" for r in res.values()), \
        {a: (r["status"], r.get("error")) for a, r in res.items()}
    assert not (tmp_path / "cot3.json").exists()
    for a in ("seed", "rrsi", "gepa", "metaharness"):
        assert len(res[a]["per_task"]) == 24 and res[a]["k"] == 1
    for a in cl.SEARCHED:
        assert res[a]["leak_check"]["passed"]
    assert len(res["pilot"]["per_task"]) == 5 * 23
    assert res["bestofn"]["n"] == round(res["harness2"]["calls_per_task"] - 1)
    assert merge.main(["--dir", str(tmp_path), "--reps", "300"]) == 0
    m = json.loads((tmp_path / "merged.json").read_text())
    assert m["arms"]["rrsi"]["vs_seed"]["n"] == 24 and "vs_cot3" not in m["arms"]["rrsi"]


def test_cot3_is_refused_with_a_clear_error(exp):
    cl, _ = exp
    with pytest.raises(SystemExit, match="no official CoT prompts"):
        cl.parse_args(["--arm", "cot3", "--offline"])


def test_bbeh_defaults_and_subsample(exp, tmp_path):
    cl, _ = exp
    a = cl.parse_args(["--arm", "seed", "--offline"])
    assert (a.evolve, a.holdout, a.ood, a.k, a.n_sub, a.rrsi_T, a.gepa_B, a.mh_iterations, a.mh_k,
            a.mh_session_usd) == (4, 6, 6, 1, 24, 4, 150, 3, 2, 0.75)
    assert a.out_dir.parts[-2:] == ("bbeh", "offline") and a.families.endswith("experiments/bbeh/families.json")
    _, suite = cl.build_suite(a)
    sub = cl.subsample(suite, a.n_sub, 0)
    assert Counter(t.id.split("-")[0] for t in sub) == {"holdout": 12, "ood": 12}
    assert set(Counter(t.family for t in sub).values()) == {2}
    s = cl.parse_args(["--arm", "seed", "--offline", "--smoke"])
    assert len(cl.build_suite(s)[1].splits["holdout"]) + len(cl.build_suite(s)[1].splits["ood"]) == 2
