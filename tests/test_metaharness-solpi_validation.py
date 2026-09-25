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
    assert '"/.solpi/' in RUNTIME_API_DOC and "KEYS ARE" in RUNTIME_API_DOC
    assert "PRE-IMPORTED" in RUNTIME_API_DOC and "event.call" in RUNTIME_API_DOC and ".id" in RUNTIME_API_DOC


def test_llm_reviewer_sees_the_runtime_api_for_solpi_harnesses():
    from rsi.core import Artifact
    from rsi.core.llm import LLM, LLMResponse, Usage
    from rsi.solpi import Idea, LLMReviewer, RUNTIME_API_DOC

    class Spy(LLM):
        def __init__(self):
            super().__init__()
            self.prompts = []

        def complete(self, prompt, **kw):
            self.prompts.append(prompt)
            return LLMResponse(text='{"verdict": "pass", "reasons": []}', usage=Usage(), model="spy")

    spy = Spy()
    base = Artifact({"harness.json": '{"extensions": {}}'})
    cand = Artifact({"harness.json": '{"extensions": {"x": {}}}', "extensions/x.py": "class MECHANISM(Extension): pass\n"})
    ok, _ = LLMReviewer(spy).review(Idea("L1", "C", "t"), base, cand)
    assert ok and RUNTIME_API_DOC in spy.prompts[0]


# ------------------------------------------------------------------ stage-B audit fixes (16, 17)
def _verbosity():
    import importlib.util
    import pathlib
    spec = importlib.util.spec_from_file_location(
        "_mhsp_gen", pathlib.Path(__file__).with_name("test_metaharness-solpi_genericity.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _RepairingProposer:
    """First implementation violates the contract; ``fix`` repairs it when told why."""

    def __init__(self):
        self.fix_errors = []

    def propose(self, idea, base, evidence, history):
        from rsi.solpi.research import MechanismProposal
        if history:
            return MechanismProposal(None, error="single variant", meta={"exhausted": True})
        cfg = json.loads(base["config.json"])
        cfg.update(verbosity=cfg["verbosity"] - 1, note="holdout")      # contract breach the reviewer catches
        return MechanismProposal(base.with_files({"config.json": json.dumps(cfg, sort_keys=True)}),
                                 change="trim1 (with a held-out reference)")

    def fix(self, idea, prop, error):
        from rsi.solpi.research import MechanismProposal
        self.fix_errors.append(error)
        if "reviewer" not in error:
            return MechanismProposal(None, error=error, meta={"exhausted": True})
        cfg = json.loads(prop.artifact["config.json"])
        cfg.pop("note")
        return MechanismProposal(prop.artifact.with_files({"config.json": json.dumps(cfg, sort_keys=True)}),
                                 change="trim1")


def test_review_rejection_routes_back_to_implementation(tmp_path):
    """Spec B3.1 / blog figure: Reviewer -> Implementation. Live r1-r3 lost whole lineage iterations because a
    rejection restarted at 01 with a fresh proposal; now the objection goes to the implementer first."""
    g = _verbosity()
    dom = g.verbosity_domain()
    prop = _RepairingProposer()
    res = sp_run(dom, g.SP_SEED, ideas=g.IDEAS[:1], proposer=prop,
                 config=SPConfig(n_lineages=1, max_iters=1, review_max=2), out_dir=tmp_path)
    r = res.meta["rounds"][0]
    assert r["survivor_ideas"] == ["G1"] and json.loads(res.best["config.json"]) == {"verbosity": 3}
    assert len(prop.fix_errors) == 1 and "holdout" in prop.fix_errors[0]
    it = r["lineages"][0]["iterations"]
    assert len(it) == 1 and it[0]["outcome"] == "frozen" and len(it[0]["review_repairs"]) == 1
    ev = load_trace(tmp_path)
    crit = [e["data"] for e in ev if e["kind"] == "critic"]
    assert [c["accept"] for c in crit] == [False, True] and crit[1]["candidate"] == "G1.0r1"
    # review_max=0 keeps the old behaviour (a rejection ends the iteration)
    prop0 = _RepairingProposer()
    res0 = sp_run(dom, g.SP_SEED, ideas=g.IDEAS[:1], proposer=prop0,
                  config=SPConfig(n_lineages=1, max_iters=1, review_max=0), out_dir=tmp_path / "r0")
    assert res0.meta["rounds"][0]["survivor_ideas"] == [] and prop0.fix_errors == []


def test_ralph_loop_sums_repair_usage_and_stops_when_exhausted():
    from rsi.core import Artifact
    from rsi.core.llm import Usage
    from rsi.solpi.research import Idea, MechanismProposal, implement

    class P:
        n = 0

        def fix(self, idea, prop, error):
            P.n += 1
            if P.n == 1:
                return MechanismProposal(Artifact({"a": "2"}), usage=Usage(1, 10, 10, 0.5))
            return MechanismProposal(Artifact({"a": "ok"}), usage=Usage(1, 10, 10, 0.25))

    first = MechanismProposal(Artifact({"a": "1"}), usage=Usage(1, 10, 10, 1.0))
    out, errs = implement(P(), Idea("X", "C", "t"), first, lambda a: None if a["a"] == "ok" else "bad", 3)
    assert out.artifact["a"] == "ok" and len(errs) == 2 and abs(out.usage.cost_usd - 1.75) < 1e-12
    assert out.usage.calls == 3

    class Q:
        calls = 0

        def fix(self, idea, prop, error):
            Q.calls += 1
            return MechanismProposal(None, error="x", meta={"exhausted": True})

    out, errs = implement(Q(), Idea("X", "C", "t"),
                          MechanismProposal(None, error="variant grid exhausted", meta={"exhausted": True}),
                          lambda a: None, 3)
    assert Q.calls == 0 and out.error == "variant grid exhausted"


def test_forked_smoke_usage_reaches_the_parent_meter():
    """Live: the interface validator's smoke ran in a forked child and its model calls ($0.02-0.03 per run)
    never reached the loop's meters; the child now ships its usage delta back."""
    from rsi.core import Artifact
    from rsi.core.llm import LLM, LLMResponse, Usage
    from rsi.metaharness.validate import InterfaceValidator

    class Paid(LLM):
        def complete(self, prompt, **kw):
            u = Usage(1, 100, 20, 0.01)
            self.meter.add(kw.get("role", "default"), u)
            return LLMResponse(text="ok", usage=u, model="paid")

    class Dom:
        def smoke(self, artifact, llm):
            llm.complete("hi", role="task")
            return None

    llm = Paid()
    ok, msg = InterfaceValidator(timeout_s=30, isolate=True).validate(Dom(), Artifact({"h.py": "x = 1\n"}), llm)
    assert ok, msg
    t = llm.meter.total()
    assert t.calls == 1 and abs(t.cost_usd - 0.01) < 1e-12 and llm.meter.by_role["task"].input_tokens == 100
