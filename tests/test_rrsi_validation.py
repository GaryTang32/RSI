"""Per-iteration tracing of RRSI: the trace covers every step, the shadow monitor is write-only
(runs with and without it make identical decisions and spend), tracing errors can never change a
run, and the independent audit re-derives every step of an offline run without a failure."""
import json

import pytest

from rsi.core import LLM, Budget, Ledger, LLMResponse, Usage
from rsi.domains.agentqa import AgentQADomain, SimModel, make_suite
from rsi.domains.harnessworld import HarnessWorldMockLLM, make_domain
from rsi.rrsi import Config, run
from rsi.rrsi.audit import audit_events, audit_run
from rsi.rrsi.mocks import AgentQAMockLLM
from rsi.trace import inspect, load_trace

KINDS = {"run_start", "baseline", "noise", "round_start", "analysis", "proposal", "critic", "eval", "gate",
         "decision", "state", "monitor", "run_end"}


def _aq(tmp, *, T=3, task=None, budget=None, **cfg):
    suite = make_suite(n_evolve=8, n_holdout=6, n_ood_per_family=2, seed=0)
    dom = AgentQADomain(suite)
    c = Config(T=T, k=2, workers=2, seed=0, record_timestamps=False, **cfg)
    res = run(dom, AgentQADomain.seed_artifact(), llm_task=task or SimModel(suite), llm_propose=AgentQAMockLLM(),
              config=c, out_dir=tmp, budget=budget)
    return dom, res


def _hw(tmp, *, T=4, **cfg):
    dom = make_domain(seed=1, n_evolve=24, n_holdout=24, n_ood_per_family=6)
    c = Config(T=T, workers=1, seed=1, record_timestamps=False, **cfg)
    res = run(dom, dom.seed_artifact(), llm_propose=HarnessWorldMockLLM(dom.world), config=c, out_dir=tmp)
    return dom, res


def _decisions(d, res) -> dict:
    nodes = {n.id: (n.status, n.score, n.cost, n.artifact_id) for n in Ledger(d / "ledger.jsonl").nodes()}
    fr = json.loads((d / "frontier.json").read_text())
    return {"ledger": nodes, "history": (d / "history.jsonl").read_text(),
            "attribution": (d / "attribution.jsonl").read_text(),
            "decisions": {p.parent.name: p.read_text() for p in sorted(d.glob("r*/decisions.json"))},
            "trajectory": fr["trajectory"], "S_star": fr["S_star"], "best": res.best.id,
            "usage": {k: v for k, v in res.usage.items()}, "stop": res.stop_reason,
            "rollouts": res.meta["n_rollouts"]}


@pytest.mark.parametrize("domain", ["agentqa", "harnessworld"])
def test_shadow_monitor_and_trace_are_write_only(tmp_path, domain):
    runner = _aq if domain == "agentqa" else _hw
    _, r_on = runner(tmp_path / "on")                                        # trace + shadow monitor (default)
    _, r_nomon = runner(tmp_path / "nomon", shadow_monitor=False)            # trace, no monitor
    _, r_off = runner(tmp_path / "off", trace=False)                         # nothing
    ref = _decisions(tmp_path / "on", r_on)
    assert _decisions(tmp_path / "nomon", r_nomon) == ref
    assert _decisions(tmp_path / "off", r_off) == ref
    kinds_on = {e["kind"] for e in load_trace(tmp_path / "on")}
    assert "monitor" in kinds_on
    assert "monitor" not in {e["kind"] for e in load_trace(tmp_path / "nomon")}
    assert not (tmp_path / "off" / "trace.jsonl").exists()
    # the monitor really ran rollouts, on its own meter: none of them entered the loop's usage
    end = [e for e in load_trace(tmp_path / "on") if e["kind"] == "run_end"][-1]["data"]
    if domain == "agentqa":
        assert end["shadow_monitor_usage"]["_total"]["calls"] > 0
    assert end["usage"] == json.loads(json.dumps(r_on.usage))


class _PaidSim(LLM):
    """SimModel answers at a fixed price per call (so a USD budget can bind offline)."""

    def __init__(self, suite):
        super().__init__()
        self.sim = SimModel(suite)
        self.name = "paid-sim"

    def _complete(self, prompt, *, system, max_tokens, seed):
        r = self.sim._complete(prompt, system=system, max_tokens=max_tokens, seed=seed)
        return LLMResponse(r.text, Usage(1, r.usage.input_tokens, r.usage.output_tokens, 0.001, 0.0), r.model)


def test_monitor_spend_never_counts_toward_the_loop_budget(tmp_path):
    suite = make_suite(n_evolve=8, n_holdout=6, n_ood_per_family=2, seed=0)
    outs = {}
    for name, mon in (("on", True), ("off", False)):
        task = _PaidSim(suite)
        # the loop alone spends ~0.05 USD per round here; the monitor would add ~0.02 per kept version
        _, res = _aq(tmp_path / name, T=6, task=task, budget=Budget(max_usd=0.09), shadow_monitor=mon)
        outs[name] = (_decisions(tmp_path / name, res), task.meter.total().cost_usd)
    assert outs["on"] == outs["off"]
    assert outs["on"][0]["stop"] == "max_usd"


def test_trace_covers_every_step_and_renders(tmp_path):
    dom, res = _aq(tmp_path / "a", T=4)
    ev = load_trace(tmp_path / "a")
    assert KINDS <= {e["kind"] for e in ev}
    assert not [e for e in ev if e["kind"] == "note" and e["data"].get("stage") == "trace_error"]
    start = ev[0]
    assert start["kind"] == "run_start" and start["data"]["seed"] == AgentQADomain.seed_artifact().short_id
    for e in ev:
        d = e["data"]
        if e["kind"] == "proposal":
            assert d["prompt"].startswith("[STABLE PREFIX") and "round_directives" in d["prompt"]
            assert d["reply"] and "outcome" in d and "declared_edits" in d
            if d["n_changes"]:
                assert d["diff"].startswith("--- a/") or d["diff"].startswith("--- /dev/null")
        if e["kind"] == "gate" and "gate_failure" not in d["math"]:
            assert {"S_prime", "S_t", "S_star", "delta", "floor", "dS", "dC", "nu", "branch", "cost_limit",
                    "shaped", "checks"} <= set(d["math"]) and d["consistent"]
        if e["kind"] in ("eval", "baseline"):
            assert d["per_task"] and all(len(v) == 2 for v in d["trials"].values())
        if e["kind"] == "round_start":
            assert {"b_t", "sigma_t", "tried_T_t", "untried_U_t", "prune_B_t", "S_star", "delta", "memory"} <= set(d)
    # one decision and one state per settled round, a monitor event per new incumbent (+ H_0)
    rounds = len(res.trajectory) - 1
    assert sum(e["kind"] == "decision" for e in ev) == rounds == sum(e["kind"] == "state" for e in ev)
    kept = {e["data"]["kept"] for e in ev if e["kind"] == "decision" and e["data"]["kept"]}
    arts = {e["data"]["summary"]["artifact"] for e in ev if e["kind"] == "eval" and e["data"]["candidate"] in kept}
    arts.add(start["data"]["seed"])
    # the monitor scores every distinct incumbent once (a revert to an earlier version is not re-scored)
    assert {e["data"]["artifact"] for e in ev if e["kind"] == "monitor"} == arts
    md = (tmp_path / "a" / "TRACE.md")
    inspect(tmp_path / "a")
    text = md.read_text()
    assert "## Round 0" in text and "**Gate on" in text and "Shadow monitor" in text


def test_loop_reads_only_evolve_and_monitor_reads_its_own_sealed_view(tmp_path):
    suite = make_suite(n_evolve=8, n_holdout=6, n_ood_per_family=2, seed=0)
    dom = AgentQADomain(suite)
    calls = []
    orig = dom.tasks.split

    def spy(name, *, allow_sealed=False):
        calls.append((name, allow_sealed))
        return orig(name, allow_sealed=allow_sealed)
    dom.tasks.split = spy
    run(dom, AgentQADomain.seed_artifact(), llm_task=SimModel(suite), llm_propose=AgentQAMockLLM(),
        config=Config(T=2, k=2, workers=2), out_dir=tmp_path / "s")
    assert {n for n, _ in calls} == {"evolve"}                        # the loop's suite: evolve only
    mon = [e["data"] for e in load_trace(tmp_path / "s") if e["kind"] == "monitor"]
    assert mon and all(set(m["sealed"]) == {"holdout", "ood"} for m in mon)
    # the split-discipline audit: no sealed id / question reached any search-LLM prompt
    rep = audit_run(tmp_path / "s", domain=dom, write=False)
    row = [r for r in rep["checks"] if r["check"].startswith("sealed holdout/ood")]
    assert row and row[0]["status"] == "pass"


def test_tracing_errors_never_change_a_run(tmp_path, monkeypatch):
    _, ref = _hw(tmp_path / "ref", T=3)
    from rsi.trace import RunTracer
    orig = RunTracer.event

    def flaky(self, kind, round=None, **data):
        if kind in ("proposal", "gate", "round_start", "monitor", "note") and data.get("stage") != "trace_error":
            raise OSError("disk full (simulated)")
        return orig(self, kind, round, **data)
    monkeypatch.setattr(RunTracer, "event", flaky)
    _, res = _hw(tmp_path / "flaky", T=3)
    assert _decisions(tmp_path / "flaky", res) == _decisions(tmp_path / "ref", ref)
    errs = [e for e in load_trace(tmp_path / "flaky") if e["data"].get("stage") == "trace_error"]
    assert errs and {e["data"]["where"] for e in errs} >= {"proposal_turns", "selection", "round_start"}


def test_audit_rederives_every_step_and_catches_a_wrong_one(tmp_path):
    dom, _ = _hw(tmp_path / "h", T=6)
    rep = audit_run(tmp_path / "h", domain=dom)
    assert rep["summary"]["fail"] == 0 and rep["summary"]["unverifiable"] == 0 and rep["summary"]["pass"] > 100
    assert (tmp_path / "h" / "audit.json").exists()
    # corrupt one recorded step: flip an admissibility verdict / a budget -> the audit must flag it
    ev = load_trace(tmp_path / "h")
    g = next(e for e in ev if e["kind"] == "gate" and "gate_failure" not in e["data"]["math"])
    g["data"]["accept"] = not g["data"]["accept"]
    rs = next(e for e in ev if e["kind"] == "round_start" and e["round"] == 2)
    rs["data"]["b_t"] += 1
    bad = audit_events(ev, domain=dom)
    failed = {r["check"] for r in bad["checks"] if r["status"] == "fail"}
    assert "admissible = Alg. 2 re-derived" in failed and "b_t = Eq. (anneal)" in failed


# ------------------------------------------------------------ stage-B audit regressions
_MARK = {"aq:self_consistency": "[rrsi:sc3]", "aq:python_tool": "[rrsi:tool]", "aq:checker": "[rrsi:checker]"}


def test_agentqa_mock_never_declares_an_edit_a_later_edit_overwrote(tmp_path):
    """Stage-B audit, offline_agentqa r0A: the mock bundled self-consistency (base=sc3) with the Python tool
    (base=tool); the second rewrote solve() and erased the first, yet both were declared and L_t credited
    '[aq:self_consistency] ... ACCEPTED'. Conflicting harness edits are no longer bundled: every declared
    idea must be visible in the candidate's diff."""
    from rsi.rrsi.mocks import AgentQAMockProfile
    suite = make_suite(n_evolve=8, n_holdout=6, n_ood_per_family=2, seed=0)
    dom = AgentQADomain(suite)
    w = {i: 1e-6 for i in ("aq:answer_format", "aq:step_by_step", "aq:verify", "aq:persona", "aq:checker",
                           "aq:python_skill", "aq:null_reword", "aq:answer_lookup")}
    w.update({"aq:self_consistency": 1e6, "aq:python_tool": 1.0})   # sc3 first, then the tool would overwrite it
    prop = AgentQAMockLLM(AgentQAMockProfile(fill_budget_p=1.0, weights=w))
    run(dom, AgentQADomain.seed_artifact(), llm_task=SimModel(suite), llm_propose=prop,
        config=Config(T=1, m=1, k=1, workers=1, seed=0, record_timestamps=False), out_dir=tmp_path)
    recs = [json.loads(l) for l in (tmp_path / "history.jsonl").read_text().splitlines()]
    cands = [r for r in recs if r.get("edit_id") and r.get("diff")]
    assert cands
    for r in cands:
        diff = (tmp_path / r["diff"]).read_text()
        for idea, mark in _MARK.items():
            if f"[{idea}]" in (r.get("hypothesis") or "") and not r["hypothesis"].startswith("prune:"):
                assert f"+    # {mark}" in diff, (idea, r["hypothesis"])
    assert any("[aq:self_consistency]" in (r.get("hypothesis") or "") for r in cands)


def test_heuristic_analyst_never_reports_solved_tasks_as_a_failure_mode():
    """Stage-B audit, offline_agentqa r3-r7: with |D| <= n_fail_traces every task fills a "fail" slot, and the
    heuristic analyst ranked 'numeric__correct_answer_v' (19 SOLVED tasks, loss 0) as the TOP failure mode of
    F_t, which the proposer then targeted. Solved traces are success evidence, never a failure mode."""
    from rsi.rrsi.analyst import Analyst
    traces = {f"t{i}": {"task_id": f"t{i}", "family": "numeric", "_role": "fail", "score": 1.0,
                        "feedback": f"Correct (answer '{i}')."} for i in range(5)}
    traces["t9"] = {"task_id": "t9", "family": "numeric", "_role": "fail", "score": 0.0,
                    "feedback": "Incorrect. Extracted answer '1'; expected '2'."}
    a = Analyst(None, mode="heuristic")
    rep = a.aggregate_heuristic(a.heuristic_digests(traces), {t: r["score"] for t, r in traces.items()})
    assert [m["affected_tasks"] for m in rep["failure_modes"]] == [["t9"]]
    assert rep["success_habits"] and rep["success_habits"][0]["n_tasks"] == 5
