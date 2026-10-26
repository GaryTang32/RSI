"""Budget limits, the trace, determinism, and ``rsi.improve(method="harness2")`` on AgentQA with the mock agent."""
from __future__ import annotations

import json

import pytest
from harness2_helpers import RecordingImprover

import rsi
from rsi.core.run import Budget
from rsi.domains.agentqa import AgentQADomain, SimModel
from rsi.harness2 import BudgetExceeded, Config, MockImprover, TaskBudget
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
