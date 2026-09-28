"""Validation-stage tests for the gene loop's run trace (rsi.trace) and shadow monitor.

* the trace and the shadow monitor are WRITE-ONLY: runs with and without them make identical decisions
  (same trajectory, same ledger statuses / scores, same library, same hub records);
* every cycle records the uniform event kinds (round_start, analysis, proposal, eval, gate, decision, state),
  and the monitor scores each new library version on the sealed holdout only through the trace;
* the ``audit`` katas layout gives every split a verified hub run needs.
"""
from __future__ import annotations

import json
from collections import Counter

import pytest

from rsi.core import SealedSplitError
from rsi.domains.katas import KataMockProposer, KatasDomain, KataSimSolver, seed_harness
from rsi.evomap import Config, SafeHub, TaskBank, run
from rsi.trace import ShadowMonitor, inspect, load_trace

HINT = " (it MUST fail before the fix and pass after it, e.g. the public check script)"


def _population(out, *, trace: bool, monitor: bool):
    dom = KatasDomain(scheme="audit")
    h = seed_harness()
    hub = SafeHub(TaskBank(dom, h, KataSimSolver(0.0, name="hub-ref"), split="test", n=24, n_off=4, k=4), seed=0)
    kw = dict(mode="safe", quarantine_min_tasks=1, trace=trace, shadow_monitor=monitor, trace_baseline_k=2,
              monitor_k=2)
    a = run(dom, h, llm_task=KataSimSolver(0.0, name="sim-a"), llm_propose=KataMockProposer(0.7),
            config=Config(cycles=12, seed=0, validation_hint=HINT, **kw), hub=hub, name="agent0",
            out_dir=out / "agent0")
    b = run(dom, h, llm_task=KataSimSolver(0.0, name="sim-b"), llm_propose=None,
            config=Config(cycles=8, seed=1, **kw), hub=hub, name="agent1", out_dir=out / "agent1")
    return a, b, hub


def _decisions(res):
    nodes = [(n.id, n.kind, n.status, n.score, n.cost, n.metrics.get("task_score")) for n in res.ledger.nodes()]
    traj = list(res.trajectory)
    return {"traj": traj, "nodes": nodes, "library": res.best["genes/library.json"]}


def _hub_state(hub):
    return [(hub.records[a].gene.id, hub.records[a].status, (hub.records[a].hub_report or {}).get("U_LCB"),
             [(r["consumer"], r["outcome"], r["counted"]) for r in hub.records[a].adoptions]) for a in hub.order]


def test_trace_and_monitor_are_write_only(tmp_path):
    a1, b1, hub1 = _population(tmp_path / "traced", trace=True, monitor=True)
    a0, b0, hub0 = _population(tmp_path / "plain", trace=False, monitor=False)
    assert (tmp_path / "traced" / "agent0" / "trace.jsonl").exists()
    assert not (tmp_path / "plain" / "agent0" / "trace.jsonl").exists()
    for x, y in ((a1, a0), (b1, b0)):
        assert _decisions(x) == _decisions(y)
    assert _hub_state(hub1) == _hub_state(hub0)
    kinds = Counter(e["kind"] for e in load_trace(tmp_path / "traced" / "agent0"))
    assert kinds["monitor"] >= 2           # the seed library + at least one evolved version


def test_monitor_alone_does_not_change_decisions(tmp_path):
    a1, b1, hub1 = _population(tmp_path / "m", trace=True, monitor=True)
    a0, b0, hub0 = _population(tmp_path / "nm", trace=True, monitor=False)
    for x, y in ((a1, a0), (b1, b0)):
        assert _decisions(x) == _decisions(y)
    assert _hub_state(hub1) == _hub_state(hub0)
    assert not [e for e in load_trace(tmp_path / "nm" / "agent0") if e["kind"] == "monitor"]


def test_every_cycle_is_fully_traced(tmp_path):
    a, b, hub = _population(tmp_path, trace=True, monitor=True)
    ev = load_trace(tmp_path / "agent0")
    assert ev[0]["kind"] == "run_start" and ev[-1]["kind"] == "run_end"
    assert {"baseline", "noise"} <= {e["kind"] for e in ev if e["round"] is None}
    for r in range(1, 13):
        ks = [e["kind"] for e in ev if e["round"] == r]
        for k in ("round_start", "analysis", "proposal", "eval", "gate", "decision", "state"):
            assert k in ks, (r, k)
        assert ks[0] == "round_start" and ks[-1] in ("state", "monitor")
    # gate arithmetic is recorded for solidify; generated genes carry the writer's prompt and reply + the real diff
    sol = [e["data"] for e in ev if e["kind"] == "gate" and e["data"].get("stage") == "solidify"]
    assert sol and all("keep_rule" in g["math"] and "validation" in g["math"] for g in sol)
    gen = [e["data"] for e in ev if e["kind"] == "proposal" and e["data"].get("source") == "generated"]
    assert gen and all(p["prompt"] and p["reply"] for p in gen)
    assert all(p["diff"].startswith("--- a/genes/active/0.md") for p in gen if not p["error"])
    # the decision's library diff matches the trajectory's library size
    dec = [e["data"] for e in ev if e["kind"] == "decision"]
    assert [len(d["library_after"]) for d in dec] == [r["n_genes"] for r in a.trajectory]
    # consumer side: quarantine A/B + critic + gate; hub side: publish gate with the bank numbers
    evb = load_trace(tmp_path / "agent1")
    assert any(e["kind"] == "critic" for e in evb)
    assert any(e["kind"] == "gate" and str(e["data"]["candidate"]).startswith("quarantine:") for e in evb)
    pub = [e["data"] for e in ev if e["kind"] == "gate" and str(e["data"]["candidate"]).startswith("hub-publish:")]
    assert pub and all("U_LCB" in p["math"] and "delta" in p["math"] for p in pub)
    md = inspect(tmp_path / "agent0")
    assert "Shadow monitor" in open(md).read()


def test_monitor_numbers_never_reach_the_agent(tmp_path):
    """The monitor evaluates sealed holdout tasks; the agent's own evaluator still refuses them."""
    dom = KatasDomain(scheme="audit")
    with pytest.raises(SealedSplitError):
        dom.tasks.split("holdout")
    res = run(dom, seed_harness(), llm_task=KataSimSolver(0.0), llm_propose=KataMockProposer(0.7),
              config=Config(cycles=4, validation_hint=HINT, trace_baseline_k=1), out_dir=tmp_path)
    agent_meta = json.dumps({k: v for k, v in res.meta.items() if k != "agent"}, default=str)
    mon = [e for e in load_trace(tmp_path) if e["kind"] == "monitor"]
    assert mon and "holdout" in mon[0]["data"]["sealed"]
    assert "holdout" not in agent_meta.replace('"holdout"', "")    # no sealed score in the result meta


def test_audit_scheme_layout():
    dom = KatasDomain(scheme="audit")
    sp = {s: len(v) for s, v in dom.tasks.splits.items()}
    assert sp["evolve"] == 5 and sp["val"] == 5 and sp["test"] == 10 and sp["holdout"] == 5
    ids = [set(v) for k, v in dom.tasks.splits.items() if k != "smoke"]
    assert sum(len(x) for x in ids) == len(set().union(*ids)) == 25


def test_katas_smoke_test_is_discriminative_under_python_and_pytest():
    """Live finding: the writer prompt allows `pytest -q smoke_test.py`, which used to exit 5 (no tests collected)
    on a module-level-assert script, so a correct gene was rejected. Both commands must fail on W0, pass on W1."""
    from rsi.core import Execution
    from rsi.domains.katas.katas import KATA_BY_ID
    from rsi.evomap.validation import SubprocessExecutor
    dom = KatasDomain()
    t = next(t for t in dom.tasks.split("evolve") if t.id == "same_text")
    w0, w1 = dom.pre_workspace(t), dom.workspace(t, Execution(output=KATA_BY_ID["same_text"].correct))
    ex = SubprocessExecutor(cache=False)
    for cmd in (["python", "smoke_test.py"], ["pytest", "-q", "smoke_test.py"]):
        assert ex(cmd, w0).exit != 0 and ex(cmd, w1).exit == 0, cmd


def test_uplift_gate_rejects_zero_uplift_at_zero_delta():
    """Live finding: a bank at ceiling calibrates delta = 0, and `U_LCB >= delta` verified a gene with U = 0."""
    from rsi.core import GateContext, Scored
    from rsi.evomap import UpliftLCB
    base = Scored(1.0, per_task={"a": 1.0, "b": 1.0})
    same = Scored(1.0, per_task={"a": 1.0, "b": 1.0})
    better = Scored(1.0, per_task={"a": 1.0, "b": 1.0})
    assert not UpliftLCB().check(same, base, GateContext(delta=0.0)).accept
    low = Scored(0.0, per_task={"a": 0.0, "b": 0.0})
    assert UpliftLCB().check(better, low, GateContext(delta=0.0)).accept


def test_cycle_tokens_not_double_counted_with_one_llm(tmp_path):
    """Live finding: with one LLM as solver and gene writer, a cycle's tokens counted the solve twice."""
    from rsi.core import MockLLM
    solver, writer = KataSimSolver(0.0), KataMockProposer(0.7)

    def respond(p, s, seed, i):
        t = writer if "STRATEGY GENE" in p else solver
        return t.complete(p, system=s, seed=seed).text
    one = MockLLM(respond, name="one")
    res = run(KatasDomain(scheme="audit"), seed_harness(), llm_task=one, llm_propose=one,
              config=Config(cycles=5, validation_hint=HINT, trace=False))
    tot = one.meter.snapshot()
    assert sum(r["tokens"] for r in res.trajectory) <= tot["_total"]["total_tokens"]
