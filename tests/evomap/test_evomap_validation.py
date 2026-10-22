"""Run trace and shadow monitor: write-only, every cycle traced, sealed splits kept from the agent."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from evomap_helpers import HINT, katas_population
from rsi.core import Execution, GateContext, MockLLM, Scored, SealedSplitError
from rsi.domains.katas import KataMockProposer, KatasDomain, KataSimSolver, seed_harness
from rsi.domains.katas.katas import KATA_BY_ID
from rsi.evomap import Config, UpliftLCB, run
from rsi.evomap.validation import SubprocessExecutor
from rsi.trace import inspect, load_trace

ROUND_KINDS = {"round_start", "analysis", "proposal", "eval", "gate", "decision", "state"}


def _decisions(res):
    nodes = [(n.id, n.kind, n.status, n.score, n.cost, n.metrics.get("task_score")) for n in res.ledger.nodes()]
    return {"traj": list(res.trajectory), "nodes": nodes, "library": res.best["genes/library.json"]}


def _hub_state(hub):
    return [(rec.gene.id, rec.status, (rec.hub_report or {}).get("U_LCB"),
             [(r["consumer"], r["outcome"], r["counted"]) for r in rec.adoptions])
            for rec in (hub.records[a] for a in hub.order)]


@pytest.mark.parametrize("trace, monitor", [(False, False), (True, False)], ids=["plain", "trace_only"])
def test_trace_and_monitor_are_write_only(traced_population, tmp_path, trace, monitor):
    pop = traced_population
    a, b, hub = katas_population(tmp_path, trace=trace, monitor=monitor)
    assert [_decisions(pop.a), _decisions(pop.b)] == [_decisions(a), _decisions(b)]
    assert _hub_state(pop.hub) == _hub_state(hub)
    assert Counter(e["kind"] for e in load_trace(pop.out / "agent0"))["monitor"] >= 2  # seed + an evolved library
    assert (tmp_path / "agent0" / "trace.jsonl").exists() == trace
    if trace:
        assert not [e for e in load_trace(tmp_path / "agent0") if e["kind"] == "monitor"]


def test_every_cycle_is_fully_traced(traced_population):
    out = traced_population.out
    ev = load_trace(out / "agent0")
    assert ev[0]["kind"] == "run_start" and ev[-1]["kind"] == "run_end"
    assert {"baseline", "noise"} <= {e["kind"] for e in ev if e["round"] is None}
    for r in range(1, 13):
        ks = [e["kind"] for e in ev if e["round"] == r]
        assert ROUND_KINDS <= set(ks), r
        assert ks[0] == "round_start" and ks[-1] in ("state", "monitor")
    sol = [e["data"] for e in ev if e["kind"] == "gate" and e["data"].get("stage") == "solidify"]
    assert sol and all("keep_rule" in g["math"] and "validation" in g["math"] for g in sol)
    gen = [e["data"] for e in ev if e["kind"] == "proposal" and e["data"].get("source") == "generated"]
    assert gen and all(p["prompt"] and p["reply"] for p in gen)
    assert all(p["diff"].startswith("--- a/genes/active/0.md") for p in gen if not p["error"])
    dec = [e["data"] for e in ev if e["kind"] == "decision"]
    assert [len(d["library_after"]) for d in dec] == [r["n_genes"] for r in traced_population.a.trajectory]
    evb = load_trace(out / "agent1")
    assert any(e["kind"] == "critic" for e in evb)
    assert any(e["kind"] == "gate" and str(e["data"]["candidate"]).startswith("quarantine:") for e in evb)
    pub = [e["data"] for e in ev if e["kind"] == "gate" and str(e["data"]["candidate"]).startswith("hub-publish:")]
    assert pub and all("U_LCB" in p["math"] and "delta" in p["math"] for p in pub)
    assert "Shadow monitor" in Path(inspect(out / "agent0")).read_text()


def test_monitor_numbers_never_reach_the_agent(tmp_path):
    dom = KatasDomain(scheme="audit")
    with pytest.raises(SealedSplitError):
        dom.tasks.split("holdout")
    res = run(dom, seed_harness(), llm_task=KataSimSolver(0.0), llm_propose=KataMockProposer(0.7),
              config=Config(cycles=4, validation_hint=HINT, trace_baseline_k=1), out_dir=tmp_path)
    agent_meta = json.dumps({k: v for k, v in res.meta.items() if k != "agent"}, default=str)
    mon = [e for e in load_trace(tmp_path) if e["kind"] == "monitor"]
    assert mon and "holdout" in mon[0]["data"]["sealed"]
    assert "holdout" not in agent_meta.replace('"holdout"', "")  # no sealed score in the result meta


def test_audit_scheme_layout():
    dom = KatasDomain(scheme="audit")
    sp = {s: len(v) for s, v in dom.tasks.splits.items()}
    assert sp["evolve"] == 5 and sp["val"] == 5 and sp["test"] == 10 and sp["holdout"] == 5
    ids = [set(v) for k, v in dom.tasks.splits.items() if k != "smoke"]
    assert sum(len(x) for x in ids) == len(set().union(*ids)) == 25


def test_katas_smoke_test_is_discriminative_under_python_and_pytest():
    """`pytest -q` on a module-level-assert script must not exit 5 (no tests collected) on a correct fix."""
    dom = KatasDomain()
    t = next(t for t in dom.tasks.split("evolve") if t.id == "same_text")
    w0, w1 = dom.pre_workspace(t), dom.workspace(t, Execution(output=KATA_BY_ID["same_text"].correct))
    ex = SubprocessExecutor(cache=False)
    for cmd in (["python", "smoke_test.py"], ["pytest", "-q", "smoke_test.py"]):
        assert ex(cmd, w0).exit != 0 and ex(cmd, w1).exit == 0, cmd


def test_uplift_gate_rejects_zero_uplift_at_zero_delta():
    def flat(s):
        return Scored(s, per_task={"a": s, "b": s})

    assert not UpliftLCB().check(flat(1.0), flat(1.0), GateContext(delta=0.0)).accept
    assert UpliftLCB().check(flat(1.0), flat(0.0), GateContext(delta=0.0)).accept


def test_cycle_tokens_not_double_counted_with_one_llm():
    solver, writer = KataSimSolver(0.0), KataMockProposer(0.7)

    def respond(p, s, seed, i):
        return (writer if "STRATEGY GENE" in p else solver).complete(p, system=s, seed=seed).text

    one = MockLLM(respond, name="one")
    res = run(KatasDomain(scheme="audit"), seed_harness(), llm_task=one, llm_propose=one,
              config=Config(cycles=5, validation_hint=HINT, trace=False))
    assert sum(r["tokens"] for r in res.trajectory) <= one.meter.snapshot()["_total"]["total_tokens"]
