"""Per-iteration trace, write-only shadow monitor, harmless tracing errors, and the step-by-step audit."""
import json
from functools import partial

import pytest

from rsi.core import Budget
from rsi.domains.agentqa import AgentQADomain, SimModel
from rsi.rrsi import Analyst, Config, run
from rsi.rrsi.audit import audit_events, audit_run
from rsi.rrsi.mocks import AgentQAMockLLM, AgentQAMockProfile
from rsi.trace import RunTracer, inspect, load_trace

from rrsi_helpers import Paid, aq_run, aq_suite, hw_run, read_jsonl, snapshot, spy_splits

KINDS = {"run_start", "baseline", "noise", "round_start", "analysis", "proposal", "critic", "eval", "gate",
         "decision", "state", "monitor", "run_end"}
MARKS = {"aq:self_consistency": "[rrsi:sc3]", "aq:python_tool": "[rrsi:tool]", "aq:checker": "[rrsi:checker]"}


_hw = partial(hw_run, seed=1, T=4)


def _outcome(d, res) -> dict:
    return snapshot(d) | {"best": res.best.id, "usage": dict(res.usage), "stop": res.stop_reason,
                          "rollouts": res.meta["n_rollouts"]}


@pytest.mark.parametrize("runner", [aq_run, _hw], ids=["agentqa", "harnessworld"])
def test_shadow_monitor_and_trace_are_write_only(tmp_path, runner):
    _, on = runner(tmp_path / "on")
    _, nomon = runner(tmp_path / "nomon", shadow_monitor=False)
    _, off = runner(tmp_path / "off", trace=False)
    ref = _outcome(tmp_path / "on", on)
    assert _outcome(tmp_path / "nomon", nomon) == ref
    assert _outcome(tmp_path / "off", off) == ref
    assert "monitor" in {e["kind"] for e in load_trace(tmp_path / "on")}
    assert "monitor" not in {e["kind"] for e in load_trace(tmp_path / "nomon")}
    assert not (tmp_path / "off" / "trace.jsonl").exists()
    end = [e for e in load_trace(tmp_path / "on") if e["kind"] == "run_end"][-1]["data"]
    if runner is aq_run:                                  # the monitor ran rollouts on its own meter
        assert end["shadow_monitor_usage"]["_total"]["calls"] > 0
    assert end["usage"] == json.loads(json.dumps(on.usage))


def test_monitor_spend_never_counts_toward_the_loop_budget(tmp_path):
    outs = {}
    for name, mon in (("on", True), ("off", False)):
        task = Paid(SimModel(aq_suite()), 0.001)
        # the loop alone spends ~0.05 USD per round; the monitor would add ~0.02 per kept version
        _, res = aq_run(tmp_path / name, T=6, task=task, budget=Budget(max_usd=0.09), shadow_monitor=mon)
        outs[name] = (_outcome(tmp_path / name, res), task.meter.total().cost_usd)
    assert outs["on"] == outs["off"]
    assert outs["on"][0]["stop"] == "max_usd"


def test_trace_covers_every_step_and_renders(tmp_path):
    _, res = aq_run(tmp_path / "a", T=4)
    ev = load_trace(tmp_path / "a")
    assert KINDS <= {e["kind"] for e in ev}
    assert not [e for e in ev if e["kind"] == "note" and e["data"].get("stage") == "trace_error"]
    start = ev[0]
    assert start["kind"] == "run_start" and start["data"]["seed"] == AgentQADomain.seed_artifact().short_id
    for e in ev:
        kind, d = e["kind"], e["data"]
        if kind == "proposal":
            assert d["prompt"].startswith("[STABLE PREFIX") and "round_directives" in d["prompt"]
            assert d["reply"] and "outcome" in d and "declared_edits" in d
            if d["n_changes"]:
                assert d["diff"].startswith("--- a/") or d["diff"].startswith("--- /dev/null")
        if kind == "gate" and "gate_failure" not in d["math"]:
            assert {"S_prime", "S_t", "S_star", "delta", "floor", "dS", "dC", "nu", "branch", "cost_limit",
                    "shaped", "checks"} <= set(d["math"]) and d["consistent"]
        if kind in ("eval", "baseline"):
            assert d["per_task"] and all(len(v) == 2 for v in d["trials"].values())
        if kind == "round_start":
            assert {"b_t", "sigma_t", "tried_T_t", "untried_U_t", "prune_B_t", "S_star", "delta", "memory"} <= set(d)
    rounds = len(res.trajectory) - 1
    assert sum(e["kind"] == "decision" for e in ev) == rounds == sum(e["kind"] == "state" for e in ev)
    kept = {e["data"]["kept"] for e in ev if e["kind"] == "decision" and e["data"]["kept"]}
    arts = {e["data"]["summary"]["artifact"] for e in ev if e["kind"] == "eval" and e["data"]["candidate"] in kept}
    # the monitor scores every distinct incumbent (and H_0) once; a revert is not re-scored
    assert {e["data"]["artifact"] for e in ev if e["kind"] == "monitor"} == arts | {start["data"]["seed"]}
    inspect(tmp_path / "a")
    text = (tmp_path / "a" / "TRACE.md").read_text()
    assert "## Round 0" in text and "**Gate on" in text and "Shadow monitor" in text


def test_loop_reads_only_evolve_and_monitor_reads_its_own_sealed_view(tmp_path):
    suite = aq_suite()
    dom = AgentQADomain(suite)
    calls = spy_splits(dom)
    run(dom, AgentQADomain.seed_artifact(), llm_task=SimModel(suite), llm_propose=AgentQAMockLLM(),
        config=Config(T=2, k=2, workers=2), out_dir=tmp_path / "s")
    assert {n for n, _ in calls} == {"evolve"}
    mon = [e["data"] for e in load_trace(tmp_path / "s") if e["kind"] == "monitor"]
    assert mon and all(set(m["sealed"]) == {"holdout", "ood"} for m in mon)
    rep = audit_run(tmp_path / "s", domain=dom, write=False)       # no sealed id reached a search-LLM prompt
    row = [r for r in rep["checks"] if r["check"].startswith("sealed holdout/ood")]
    assert row and row[0]["status"] == "pass"


def test_tracing_errors_never_change_a_run(tmp_path, monkeypatch):
    _, ref = _hw(tmp_path / "ref", T=3)
    orig = RunTracer.event

    def flaky(self, kind, round=None, **data):
        if kind in ("proposal", "gate", "round_start", "monitor", "note") and data.get("stage") != "trace_error":
            raise OSError("disk full (simulated)")
        return orig(self, kind, round, **data)
    monkeypatch.setattr(RunTracer, "event", flaky)
    _, res = _hw(tmp_path / "flaky", T=3)
    assert _outcome(tmp_path / "flaky", res) == _outcome(tmp_path / "ref", ref)
    errs = [e for e in load_trace(tmp_path / "flaky") if e["data"].get("stage") == "trace_error"]
    assert errs and {e["data"]["where"] for e in errs} >= {"proposal_turns", "selection", "round_start"}


def test_audit_rederives_every_step_and_catches_a_wrong_one(tmp_path):
    dom, _ = _hw(tmp_path / "h", T=6)
    rep = audit_run(tmp_path / "h", domain=dom)
    assert rep["summary"]["fail"] == 0 and rep["summary"]["unverifiable"] == 0 and rep["summary"]["pass"] > 100
    assert (tmp_path / "h" / "audit.json").exists()
    ev = load_trace(tmp_path / "h")
    g = next(e for e in ev if e["kind"] == "gate" and "gate_failure" not in e["data"]["math"])
    g["data"]["accept"] = not g["data"]["accept"]
    next(e for e in ev if e["kind"] == "round_start" and e["round"] == 2)["data"]["b_t"] += 1
    failed = {r["check"] for r in audit_events(ev, domain=dom)["checks"] if r["status"] == "fail"}
    assert "admissible = Alg. 2 re-derived" in failed and "b_t = Eq. (anneal)" in failed


def test_agentqa_mock_never_declares_an_edit_a_later_edit_overwrote(tmp_path):
    w = {i: 1e-6 for i in ("aq:answer_format", "aq:step_by_step", "aq:verify", "aq:persona", "aq:checker",
                           "aq:python_skill", "aq:null_reword", "aq:answer_lookup")}
    w.update({"aq:self_consistency": 1e6, "aq:python_tool": 1.0})   # sc3 first; the tool would overwrite solve()
    prop = AgentQAMockLLM(AgentQAMockProfile(fill_budget_p=1.0, weights=w))
    aq_run(tmp_path, T=1, k=1, workers=1, propose=prop, m=1)
    cands = [r for r in read_jsonl(tmp_path / "history.jsonl") if r.get("edit_id") and r.get("diff")]
    assert cands
    for r in cands:
        diff, hyp = (tmp_path / r["diff"]).read_text(), r.get("hypothesis") or ""
        for idea, mark in MARKS.items():
            if f"[{idea}]" in hyp and not hyp.startswith("prune:"):
                assert f"+    # {mark}" in diff, (idea, hyp)
    assert any("[aq:self_consistency]" in (r.get("hypothesis") or "") for r in cands)


def test_heuristic_analyst_never_reports_solved_tasks_as_a_failure_mode():
    traces = {f"t{i}": {"task_id": f"t{i}", "family": "numeric", "_role": "fail", "score": 1.0,
                        "feedback": f"Correct (answer '{i}')."} for i in range(5)}
    traces["t9"] = {"task_id": "t9", "family": "numeric", "_role": "fail", "score": 0.0,
                    "feedback": "Incorrect. Extracted answer '1'; expected '2'."}
    a = Analyst(None, mode="heuristic")
    rep = a.aggregate_heuristic(a.heuristic_digests(traces), {t: r["score"] for t, r in traces.items()})
    assert [m["affected_tasks"] for m in rep["failure_modes"]] == [["t9"]]
    assert rep["success_habits"] and rep["success_habits"][0]["n_tasks"] == 5
