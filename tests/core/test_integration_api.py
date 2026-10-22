"""Unified API, CLI and trace/inspector (offline)."""
from __future__ import annotations

import json

import pytest

import rsi
from rsi.api import METHODS, make_config, method_module, recommend
from rsi.cli import main as cli_main
from rsi.core import Evaluator
from rsi.domains.agentqa import AgentQADomain, SimModel, make_suite
from rsi.rrsi.mocks import AgentQAMockLLM
from rsi.trace import RunTracer, ShadowMonitor, inspect, load_trace, monitor_table, render_markdown


def test_every_method_importable_with_config_and_run():
    for name in METHODS:
        assert callable(method_module(name).run)
        make_config(name)


def test_make_config_rejects_unknown_fields():
    with pytest.raises(TypeError):
        make_config("rrsi", {"definitely_not_a_field": 1})


def test_recommend_orders_by_problem():
    assert recommend(prompts_only=True)[0][0] == "gepa"
    assert recommend(single_metric_script=True)[0][0] == "autoresearch"
    assert "solpi" in [m for m, _ in recommend(cost_is_bottleneck=True)]
    assert recommend(what_changes="nothing-special")[0][0] == "rrsi"


def test_improve_rrsi_offline_generalizes():
    suite = make_suite(seed=0)
    dom = AgentQADomain(suite)
    res = rsi.improve(dom, method="rrsi", llm_task=SimModel(suite), llm_propose=AgentQAMockLLM(), config={"T": 4})
    assert res.best != res.baseline
    rep = rsi.transfer_report(dom, SimModel(suite), {"seed": res.baseline, "rrsi": res.best})
    for split in ("holdout", "ood"):
        assert rep["splits"][split]["rrsi"]["S"] > rep["splits"][split]["seed"]["S"]


def test_cli_methods_and_improve(tmp_path, capsys):
    assert cli_main(["methods"]) == 0
    assert "rrsi" in capsys.readouterr().out
    out = tmp_path / "run"
    assert cli_main(["improve", "--method", "rrsi", "--problem", "agentqa", "--llm", "sim", "--out", str(out),
                     "--set", "T=2"]) == 0
    assert (out / "transfer.json").exists() and (out / "summary.json").exists()
    assert set(json.loads((out / "transfer.json").read_text())["splits"]) >= {"evolve", "holdout", "ood"}


def test_cli_offline_unsupported_combo_explains(tmp_path):
    with pytest.raises(SystemExit) as e:
        cli_main(["improve", "--method", "dream", "--problem", "agentqa", "--llm", "sim", "--out", str(tmp_path)])
    assert "offline" in str(e.value)


def test_tracer_monitor_and_inspector(tmp_path):
    suite = make_suite(seed=1)
    dom, sim = AgentQADomain(suite), SimModel(suite)
    tr = RunTracer(tmp_path, "unit")
    tr.monitor = ShadowMonitor(dom, sim, splits=("holdout", "ood"))
    seed = dom.seed_artifact()
    ev = Evaluator(dom, sim)
    tr.event("run_start", seed=seed.short_id, config={"x": 1})
    tr.evaluation(0, "H0", ev.evaluate(seed, "evolve"))
    new = seed.with_files({"prompts/task.md": "{question}\nEnd with 'ANSWER: <value>'.\n"})
    tr.proposal(1, "c1", parent="H0", prompt="p" * 10, reply="r", change="format line", diff=seed.diff(new))
    tr.gate(1, "c1", True, "ok", math={"dS": 0.1})
    tr.decision(1, kept="c1", incumbent_before="H0", incumbent_after="c1")
    tr.kept(1, "c1", new, decision_score=0.5)
    tr.kept(1, "c1", new, decision_score=0.5)          # same artifact: monitored once
    events = load_trace(tmp_path)
    assert [e["kind"] for e in events].count("monitor") == 1
    rows = monitor_table(events)
    assert rows and "holdout" in rows[0] and "ood" in rows[0]
    md = render_markdown(events)
    assert "Round 1" in md and "Shadow monitor" in md and "format line" in md
    assert inspect(tmp_path).endswith("TRACE.md")
    with pytest.raises(Exception):                     # the loop's own evaluator still refuses sealed splits
        ev.evaluate(seed, "holdout")


def test_tracer_disabled_is_noop():
    tr = RunTracer(None, "x")
    tr.event("note", 0, a=1)
    assert not tr.enabled
