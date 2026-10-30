"""The autoresearch, SoL-Pi, Dream-RSI and EvoMap arms of experiments/bbeh, offline; answer length in merge.py."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from rsi.domains.bbeh import make_pilot_suite, make_suite

ROOT = Path(__file__).resolve().parents[2]
SMALL = ["--evolve", "2", "--holdout", "2", "--ood", "2", "--workers", "2", "--ar-experiments", "2",
         "--solpi-lineages", "2", "--dream-rounds", "2", "--dream-branches", "1", "--evomap-cycles", "4"]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def exp(bbeh_offline):
    return (_load("bbeh_cl_later", "experiments/bbeh/compare_live.py"), _load("bbeh_merge_later", "experiments/bbeh/merge.py"),
            _load("bbh_rescore_later", "experiments/bbh/rescore_tokens.py"))


@pytest.mark.parametrize("arm", ["autoresearch", "solpi", "dream", "evomap"])
def test_each_later_arm_runs_offline_and_is_scored_like_the_searched_arms(exp, tmp_path, arm):
    cl, _, _ = exp
    o = cl.run_arm(cl.parse_args(["--arm", arm, "--offline", "--out-dir", str(tmp_path), *SMALL]), arm)
    assert o["status"] == "ok", (o.get("error"), o.get("traceback"))
    assert o["leak_check"]["passed"] and Path(o["final_harness"]["dir"]).is_dir()
    assert len(o["per_task"]) == 24 and o["k"] == 2 and {p["phase"] for p in o["phases"]} >= {"search", "eval_ood"}
    assert all(v is not None and v >= 0 for v in o["per_task_out_tokens"].values())
    assert "firewall" not in o["suite"]["summary"]          # comparable with every other arm in merge.py
    s = o["search"]
    if arm == "autoresearch":
        assert s["keep_rule"].startswith("gain > delta") and s["noise_delta_k2"] > 0 and s["k"] == 2
    if arm == "solpi":
        assert s["firewall_split"] == "firewall" and o["suite"]["firewall"]["n"] == 12 and s["ideas"]
    if arm == "dream":
        assert s["agent_calls"] <= s["rounds"] * s["attempts_per_round"] and len(s["trajectory"]) == 2
    if arm == "evomap":
        assert s["cycles"] == 4 and "genes/library.json" in o["final_harness"]["files"]


def test_firewall_split_is_sealed_disjoint_and_leaves_the_other_splits_unchanged(bkw):
    base = make_suite(per_family={"evolve": 4, "holdout": 8, "ood": 8}, **bkw)
    fw = make_suite(per_family={"evolve": 4, "holdout": 8, "ood": 8, "firewall": 2}, **bkw)
    assert all(fw.splits[k] == base.splits[k] for k in base.splits) and fw.is_sealed("firewall")
    ids = fw.splits["firewall"]
    assert len(ids) == 12 and fw.families("firewall") == fw.families("evolve")
    used = {(fw.get(t).family, fw.get(t).meta["bbh_index"]) for s in ("evolve", "holdout", "ood") for t in fw.splits[s]}
    pilot = {(t.family, t.meta["bbh_index"]) for t in make_pilot_suite(**bkw).split("pilot")}
    mine = {(fw.get(t).family, fw.get(t).meta["bbh_index"]) for t in ids}
    assert not mine & used and not mine & pilot


def test_cap_stops_a_later_arm_before_its_search(exp, tmp_path):
    cl, _, _ = exp
    o = cl.run_arm(cl.parse_args(["--arm", "dream", "--offline", "--cap", "0", "--out-dir", str(tmp_path), *SMALL]),
                   "dream")
    assert o["status"] == "cap_reached" and "before search" in o["stop"]


def test_merge_shows_tokens_and_rescore_fills_older_arms_from_the_cache_only(exp, tmp_path, capsys):
    cl, merge, rescore = exp
    cache = tmp_path / "cache"
    live = ["--model", "mock", "--cache-dir", str(cache), "--out-dir", str(tmp_path), *SMALL]
    for arm in ("seed", "solpi"):
        assert cl.run_arm(cl.parse_args(["--arm", arm, *live]), arm)["status"] == "ok"
    # an arm scored before tokens were recorded: drop its token fields, then replay its eval from the cache
    seed = json.loads((tmp_path / "seed.json").read_text())
    want = seed.pop("per_task_out_tokens")
    seed.pop("per_task_usd")
    (tmp_path / "seed.json").write_text(json.dumps(seed))
    before = (tmp_path / "seed.json").read_text()
    assert rescore.main(["--benchmark", "bbeh", "--arm", "seed", "--dir", str(tmp_path), "--cache-dir", str(cache), "--workers", "2"]) == 0
    side = json.loads((tmp_path / "seed.tokens.json").read_text())
    assert side["per_task_out_tokens"] == want and side["cache_misses"] == 0
    assert (tmp_path / "seed.json").read_text() == before                   # the arm's own file is untouched
    assert merge.main(["--dir", str(tmp_path), "--reps", "200"]) == 0
    m = json.loads((tmp_path / "merged.json").read_text())
    assert m["arms"]["seed"]["out_tokens_per_example"] > 0 and m["arms"]["solpi"]["out_tokens_vs_seed"]["n"] == 24
    assert "output tokens per answer" in capsys.readouterr().out
