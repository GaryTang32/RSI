"""The Meta-Harness / SoL-Pi audit trace is complete and WRITE-ONLY.

* runs with and without the shadow monitor (and with the trace switched off) make identical
  decisions: same ledger nodes (status, score, cost, artifact) and the same result;
* the trace carries every uniform event kind with the numbers needed to re-check each step
  (actual diffs equal the ledger's diffs, gate arithmetic, frontier membership);
* the shadow monitor evaluates sealed splits only through its own evaluator and its model
  calls are metered under ``shadow:*``.
"""
from __future__ import annotations

import json

import pytest

from rsi.domains.agentworld import MockAgentLLM
from rsi.domains.agentworld import make_domain as make_world
from rsi.domains.memoclassify import make_domain as make_memo
from rsi.metaharness import Config as MHConfig
from rsi.metaharness import run as mh_run
from rsi.metaharness.frontier import pareto_frontier
from rsi.solpi import Config as SPConfig
from rsi.solpi import GateSpec
from rsi.solpi import run as sp_run
from rsi.trace import inspect, load_trace


def _ledger_sig(res):
    return [(n.id, n.parent, n.status, None if n.score is None else round(n.score, 12),
             None if n.cost is None else round(n.cost, 6), n.artifact_id) for n in res.ledger.nodes()]


# ------------------------------------------------------------------ Meta-Harness
def _mh(tmp_path, name, **kw):
    dom = make_memo(seed=0, scale=0.4)
    cfg = MHConfig(iterations=2, k=2, seed=0, **{k: v for k, v in kw.items() if k != "monitor"})
    return mh_run(dom, dom.seed_artifact("fewshot_all"), llm_task=dom.make_model("A"), llm_propose=None,
                  config=cfg, out_dir=tmp_path / name, baselines=dom.baselines(), monitor=kw.get("monitor"))


@pytest.fixture(scope="module")
def mh_runs(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("mh")
    return tmp, {"monitor": _mh(tmp, "monitor"), "nomonitor": _mh(tmp, "nomonitor", monitor=False),
                 "notrace": _mh(tmp, "notrace", trace=False)}


def test_mh_monitor_is_write_only(mh_runs):
    tmp, r = mh_runs
    sig = _ledger_sig(r["monitor"])
    assert sig == _ledger_sig(r["nomonitor"]) == _ledger_sig(r["notrace"])
    assert r["monitor"].best.id == r["nomonitor"].best.id == r["notrace"].best.id
    fin = [x.meta["final"]["splits"]["test"]["results"] for x in r.values()]
    assert fin[0] == fin[1] == fin[2]
    ev_on = load_trace(tmp / "monitor")
    assert any(e["kind"] == "monitor" for e in ev_on)
    assert not any(e["kind"] == "monitor" for e in load_trace(tmp / "nomonitor"))
    assert not (tmp / "notrace" / "trace.jsonl").exists()
    # the monitor looks at sealed ood only (test is left to finalize, which runs it once)
    for e in ev_on:
        if e["kind"] == "monitor":
            assert set(e["data"]["sealed"]) == {"ood"}


def test_mh_trace_is_complete_and_consistent(mh_runs):
    tmp, r = mh_runs
    ev = load_trace(tmp / "monitor")
    kinds = {e["kind"] for e in ev}
    for k in ("run_start", "baseline", "noise", "round_start", "analysis", "proposal", "gate", "eval", "decision",
              "state", "monitor", "note", "run_end"):
        assert k in kinds, k
    res = r["monitor"]
    store = res.loop.store
    led = {n.meta.get("system"): n for n in res.ledger.nodes()}
    for e in ev:
        d = e["data"]
        if e["kind"] == "proposal" and d["candidate"] in led:
            # the recorded diff is the actual base -> candidate diff
            base = store.artifact(d["parent"])
            assert d["diff"][:5000] == base.diff(store.artifact(d["candidate"]))[:5000]
        if e["kind"] == "eval":
            assert d["summary"]["split"] == "evolve"           # evolution touches the search split only
        if e["kind"] == "decision":
            pts = [(n, s["score"], s["context_cost"]) for n in store.names() if (s := store.scores(n))
                   and store.meta(n).get("iteration", 0) <= e["round"]]
            front = {p[0] for p in pareto_frontier(pts)}
            for n, row in d["per_candidate"].items():
                if "on_frontier" in row:
                    assert row["on_frontier"] == (n in front)
    assert inspect(tmp / "monitor").endswith("TRACE.md")


# ------------------------------------------------------------------ SoL-Pi
def _sp(tmp_path, name, **kw):
    dom = make_world(seed=0, n_train=2, n_accept=2, n_final=2, n_test=0)
    cfg = SPConfig(gate=GateSpec(mode="aggregate"), n_lineages=10, max_iters=3,
                   **{k: v for k, v in kw.items() if k != "monitor"})
    return sp_run(dom, dom.seed_artifact(), llm_task=MockAgentLLM("A"), config=cfg, out_dir=tmp_path / name,
                  monitor=kw.get("monitor"))


@pytest.fixture(scope="module")
def sp_runs(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("sp")
    return tmp, {"monitor": _sp(tmp, "monitor", monitor=True), "nomonitor": _sp(tmp, "nomonitor"),
                 "notrace": _sp(tmp, "notrace", trace=False)}


def test_solpi_monitor_is_write_only(sp_runs):
    tmp, r = sp_runs
    assert _ledger_sig(r["monitor"]) == _ledger_sig(r["nomonitor"]) == _ledger_sig(r["notrace"])
    assert r["monitor"].best.id == r["nomonitor"].best.id == r["notrace"].best.id
    keys = ("chosen", "frozen", "heldout_passed", "survivors")
    assert [{k: x.meta["rounds"][0][k] for k in keys} for x in r.values()].count(
        {k: r["monitor"].meta["rounds"][0][k] for k in keys}) == 3
    ev = load_trace(tmp / "monitor")
    mons = [e for e in ev if e["kind"] == "monitor"]
    assert mons and all(set(e["data"]["sealed"]) == {"holdout", "ood"} for e in mons)
    assert not any(e["kind"] == "monitor" for e in load_trace(tmp / "nomonitor"))
    assert not (tmp / "notrace" / "trace.jsonl").exists()


def test_solpi_trace_gate_arithmetic_and_firewall(sp_runs):
    tmp, r = sp_runs
    ev = load_trace(tmp / "monitor")
    spec = GateSpec(mode="aggregate")
    n_gate = 0
    for e in ev:
        d = e["data"]
        if e["kind"] == "gate" and "capability" in d["math"]:
            n_gate += 1
            cap = d["math"]["capability"]["score"]
            ok_cap = cap["cand"] >= cap["base"] * (1 - spec.capability[0][1]) - 1e-12
            eff = d["math"]["efficiency"]
            ok_eff = any((v["base"] - v["cand"]) / v["base"] > spec.min_gain for v in eff.values() if v["base"] > 0)
            assert d["accept"] == (ok_cap and ok_eff)
        if e["kind"] == "eval" and "summary" in d:
            # lineages only ever evaluate the training screen / rollouts (never a sealed split)
            assert d["summary"]["split"] not in ("holdout", "ood", "test")
    assert n_gate > 0
    rnd = r["monitor"].meta["rounds"][0]
    fw = [e for e in ev if e["kind"] == "gate" and "firewall" in e["data"].get("stage", "")]
    assert {e["data"]["candidate"]: e["data"]["accept"] for e in fw} == rnd["heldout_passed"]
    sink = [json.loads(l) for l in (tmp / "monitor" / "firewall_r1" / "heldout.jsonl").read_text().splitlines()]
    assert {s["candidate"]: s["passed"] for s in sink} == rnd["heldout_passed"]
    assert inspect(tmp / "monitor").endswith("TRACE.md")


# ------------------------------------------------------------------ fixes found by the from-scratch validation
def test_rewrite_proposer_maps_src_layout_to_harness_root():
    """Live haiku copied the history layout (agents/<name>/src/harness.py): the candidate got dead files and the
    harness it ran was unchanged. src/<path> is now mapped to <path> when the base has no src/ directory."""
    from rsi.core import Artifact
    from rsi.metaharness.proposer import _collect
    base = Artifact({"harness.py": "old\n", "prompts/task.md": "t\n"})
    files = {"agents/new1/src/harness.py": "new\n"}
    header = {"candidates": [{"name": "new1", "base_system": "seed", "hypothesis": "h"}]}
    [c] = _collect(files, header, 2, {"seed": base}, set(), 1)
    assert c.artifact.files == {"harness.py": "new\n", "prompts/task.md": "t\n"}
    # a base that really has a src/ directory keeps the path
    base2 = Artifact({"src/harness.py": "old\n"})
    [c2] = _collect(files, header, 2, {"seed": base2}, set(), 1)
    assert c2.artifact.files == {"src/harness.py": "new\n"}


def test_runtime_api_doc_states_preimports_and_call_id():
    """Live haiku imported Extension/ToolResult from an SDK and used event.call_id (both failed)."""
    from rsi.solpi import RUNTIME_API_DOC
    assert "PRE-IMPORTED" in RUNTIME_API_DOC and "event.call" in RUNTIME_API_DOC and ".id" in RUNTIME_API_DOC
