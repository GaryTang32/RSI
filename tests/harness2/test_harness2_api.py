"""Budget limits, the trace, determinism, and ``rsi.improve(method="harness2")`` on AgentQA with the mock agent."""
from __future__ import annotations

import json

import pytest
from harness2_helpers import RecordingImprover

import rsi
from rsi.core.run import Budget
from rsi.domains.agentqa import AgentQADomain, SimModel
from rsi.harness2 import BudgetExceeded, Config, MockImprover, TaskBudget, run
from rsi.trace import load_trace


def test_task_budget_enforces_the_identity():
    b = TaskBudget(2)
    for i in range(8):
        b.charge_rollout(f"r{i}")
    with pytest.raises(BudgetExceeded, match="R = 4k = 8"):
        b.charge_rollout("one more")
    for i in range(6):
        b.charge_session(f"s{i}")
    with pytest.raises(BudgetExceeded, match="P = 3k = 6"):
        b.charge_session("one more")
    b.assert_identity()
    with pytest.raises(BudgetExceeded, match="identity"):
        TaskBudget(1).assert_identity()


def test_global_budget_stops_between_tasks(go):
    res, _ = go(k=1, budget=Budget(max_rounds=2))
    assert res.stop_reason == "max_rounds" and len(res.trajectory) == 2
    assert len(res.meta["unprocessed"]) == res.meta["n_stream"] - 2
    res, _ = go(k=1, budget=Budget(max_rollouts=6), out="r")
    assert res.stop_reason == "max_rollouts" and len(res.trajectory) == 2      # 4 + 4 >= 6: a started task finishes


def test_spend_cap_stops_before_the_next_task_would_cross_it(go):
    imp = RecordingImprover(cost=0.01)                       # 4 improver calls per task at k = 1 -> $0.04 per task
    res, _ = go(k=1, improver=imp, max_usd=0.10)
    assert res.stop_reason == "max_usd_projected" and len(res.trajectory) == 2
    assert res.usage["_total"]["cost_usd"] <= 0.10 + 1e-9
    assert res.stop_reason != "stream_end"


def test_trace_records_every_step_and_no_score_before_the_report(go, tmp_path):
    res, _ = go(k=2, max_tasks=3, out="tr")
    ev = load_trace(tmp_path / "tr")
    kinds = [e["kind"] for e in ev]
    assert kinds[0] == "run_start" and kinds[-1] == "report" and "run_end" in kinds
    for k in ("round_start", "rollout", "proposal", "selection", "decision", "reflection", "library", "state"):
        assert k in kinds, k
    assert kinds.count("round_start") == 3 and kinds.count("rollout") == 3 * 8 and kinds.count("proposal") == 3 * 6
    rep = kinds.index("report")
    loop_text = json.dumps([e["data"] for e in ev[:rep]])
    for marker in ('"score"', '"S"', '"per_task"', '"submitted"', '"target"'):
        assert marker not in loop_text, marker
    prop = next(e["data"] for e in ev if e["kind"] == "proposal")
    assert prop["prompt"].startswith("You are adapting") and prop["reply"] and "role" in prop
    assert ev[rep]["data"]["note"].startswith("evaluation only")
    md = rsi.trace.inspect(tmp_path / "tr")
    assert "Round 0" in open(md).read()
    assert (tmp_path / "tr" / "tasks").is_dir() and (tmp_path / "tr" / "ledger.jsonl").exists()


def test_runs_are_deterministic(go):
    a, _ = go(k=2, max_tasks=4, out="a")
    b, _ = go(k=2, max_tasks=4, out="b")
    assert a.trajectory == b.trajectory and a.meta["deliverables"] == b.meta["deliverables"]
    assert a.meta["report"]["per_task"] == b.meta["report"]["per_task"]


def test_config_validation():
    for bad in (dict(k=0), dict(mode="tree"), dict(order="random"), dict(prompts={"nope": "x"}),
                dict(prompts={"work": "maximise the benchmark score"}), dict(retry_invalid=-1)):
        with pytest.raises(ValueError):
            Config(**bad).validate()
    Config(prompts={"work": "Answer short questions exactly.", "yardstick": "one exact value"}).validate()


def test_rsi_improve_harness2_on_agentqa_with_the_mock_agent(suite, tmp_path):
    dom = AgentQADomain(suite)
    res = rsi.improve(dom, method="harness2", llm_task=SimModel(suite), llm_propose=MockImprover(),
                      config={"k": 1, "split": "holdout+ood"}, out_dir=tmp_path / "imp")
    assert res.method == "harness2" and res.best == res.baseline == dom.seed_artifact()
    assert res.stop_reason == "stream_end" and len(res.trajectory) == res.meta["n_stream"] == 8
    rep = res.meta["report"]
    assert rep["n"] == 8 and set(rep["families"]) == {"numeric", "dates", "lists", "numbertheory", "strings"}
    assert rep["submitted"][0] >= rep["base"][0]
    assert all(v is not None for v in res.meta["deliverables"].values())
    assert res.usage["_total"]["calls"] > 0 and "editor" not in res.usage      # roles are inv/dom/composer/...
    assert {"inv", "dom", "composer", "reflector", "task"} <= set(res.usage)
    saved = json.loads((tmp_path / "imp" / "summary.json").read_text())
    assert saved["method"] == "harness2" and saved["meta"]["report"]["n"] == 8
    assert "harness2" in rsi.METHODS and rsi.method_module("harness-squared").__name__ == "rsi.harness2"
    with pytest.raises(TypeError, match="no fields"):
        rsi.make_config("harness2", {"rounds": 3})


def test_recommend_and_describe():
    assert rsi.recommend(no_grader=True)[0][0] == "harness2"
    assert all(m != "harness2" for m, _ in rsi.recommend())
    assert "harness2" in rsi.describe_methods()


def test_cli_improve_offline(tmp_path, capsys):
    from rsi.cli import main
    assert main(["improve", "--method", "harness2", "--problem", "agentqa", "--llm", "sim", "--out",
                 str(tmp_path / "cli"), "--set", "max_tasks=3", "k=1"]) == 0
    out = capsys.readouterr().out
    assert "stream accuracy" in out and (tmp_path / "cli" / "report.json").exists()
    assert main(["recommend", "--no-grader"]) == 0 and "harness2" in capsys.readouterr().out


def test_best_of_n_uses_the_same_stream_selector_and_rollout_seeds(dom, sim, suite):
    from rsi.harness2 import best_of_n
    from rsi.harness2.api import stream_tasks
    tasks = stream_tasks(dom, Config(split="holdout", max_tasks=3))
    sel = RecordingImprover()
    out = best_of_n(dom, dom.seed_artifact(), llm_task=sim, llm_select=sel, n=4, tasks=tasks)
    assert [r.task_id for r in out["records"]] == [t.id for t in tasks]
    assert all(len(r.rollouts) == 4 and r.adopted.startswith("base") for r in out["records"])
    assert [role for role, _ in sel.prompts] == ["selector"] * 3
    rolls = [e for e in dom.events if e[0] == "rollout"]
    assert all(target is None for _, _, target, _, _ in rolls)
    h2 = run(dom, dom.seed_artifact(), llm_task=sim, llm_propose=MockImprover(), config=Config(k=1), tasks=tasks)
    assert all(a.rollouts["base1"].seed == b.rollouts["base1"].seed
               for a, b in zip(out["records"], h2.meta["records"]))
    assert out["report"]["n"] == 3


def test_compare_sim_quick_runs_and_reports_cis(tmp_path):
    import subprocess
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    dest = tmp_path / "cmp.json"
    r = subprocess.run([sys.executable, str(root / "experiments/harness2/compare_sim.py"), "--quick", "--out",
                        str(dest)], capture_output=True, text=True, timeout=300, cwd=root)
    assert r.returncode == 0, r.stderr[-2000:]
    d = json.loads(dest.read_text())
    assert set(d["arms"]) >= {"seed", "harness2_k1", "harness2_k3", "best_of_4", "best_of_12"}
    for name, arm in d["arms"].items():
        lo, hi = arm["ci95"]
        assert lo <= arm["accuracy"] <= hi
        if name != "seed":
            assert arm["vs_seed"]["ci95"][0] <= arm["vs_seed"]["diff"] <= arm["vs_seed"]["ci95"][1]
    assert d["arms"]["harness2_k1"]["rollouts_per_task"] == d["arms"]["best_of_4"]["rollouts_per_task"] == 4
    assert d["arms"]["harness2_k3"]["rollouts_per_task"] == d["arms"]["best_of_12"]["rollouts_per_task"] == 12


def test_stored_compare_sim_result_is_consistent():
    from pathlib import Path
    p = Path(__file__).resolve().parents[2] / "results" / "harness2" / "compare_sim.json"
    if not p.exists():
        pytest.skip("results/harness2/compare_sim.json is not in this checkout (gitignored on main)")
    d = json.loads(p.read_text())
    assert d["seeds"] == 3 and d["n_tasks"] == 43
    for arm in d["arms"].values():
        assert arm["ci95"][0] <= arm["accuracy"] <= arm["ci95"][1]
        per_task = [v for ps in d["per_task"][next(k for k, a in d["arms"].items() if a is arm)] for v in ps.values()]
        assert abs(sum(per_task) / len(per_task) - arm["accuracy"]) < 1e-9
