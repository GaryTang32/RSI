"""The Meta-Harness / SoL-Pi audit trace is complete and write-only: monitoring never changes a decision."""
import json

import pytest

from rsi.core import Artifact
from rsi.core.llm import LLM, LLMResponse, Usage
from rsi.domains.agentworld import MockAgentLLM
from rsi.domains.agentworld import make_domain as make_world
from rsi.domains.memoclassify import make_domain as make_memo
from rsi.metaharness import Config as MHConfig
from rsi.metaharness import run as mh_run
from rsi.metaharness.frontier import pareto_frontier
from rsi.metaharness.proposer import _collect
from rsi.metaharness.validate import InterfaceValidator
from rsi.solpi import RUNTIME_API_DOC, GateSpec, Idea, LLMReviewer
from rsi.solpi import Config as SPConfig
from rsi.solpi import run as sp_run
from rsi.solpi.research import MechanismProposal, implement
from rsi.trace import inspect, load_trace
from solpi_testkit import VERBOSITY_IDEAS, VERBOSITY_SEED, verbosity_domain


def _ledger_sig(res):
    return [(n.id, n.parent, n.status, None if n.score is None else round(n.score, 12),
             None if n.cost is None else round(n.cost, 6), n.artifact_id) for n in res.ledger.nodes()]


def _mh(tmp_path, name, monitor=None, **kw):
    dom = make_memo(seed=0, scale=0.4)
    return mh_run(dom, dom.seed_artifact("fewshot_all"), llm_task=dom.make_model("A"), llm_propose=None,
                  config=MHConfig(iterations=2, k=2, seed=0, **kw), out_dir=tmp_path / name,
                  baselines=dom.baselines(), monitor=monitor)


def _sp(tmp_path, name, monitor=None, **kw):
    dom = make_world(seed=0, n_train=2, n_accept=2, n_final=2, n_test=0)
    cfg = SPConfig(gate=GateSpec(mode="aggregate"), n_lineages=10, max_iters=3, **kw)
    return sp_run(dom, dom.seed_artifact(), llm_task=MockAgentLLM("A"), config=cfg, out_dir=tmp_path / name,
                  monitor=monitor)


@pytest.fixture(scope="module")
def mh_runs(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("mh")
    return tmp, {"monitor": _mh(tmp, "monitor"), "nomonitor": _mh(tmp, "nomonitor", monitor=False),
                 "notrace": _mh(tmp, "notrace", trace=False)}


@pytest.fixture(scope="module")
def sp_runs(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("sp")
    return tmp, {"monitor": _sp(tmp, "monitor", monitor=True), "nomonitor": _sp(tmp, "nomonitor"),
                 "notrace": _sp(tmp, "notrace", trace=False)}


def _assert_monitor_write_only(tmp, r, sealed):
    assert _ledger_sig(r["monitor"]) == _ledger_sig(r["nomonitor"]) == _ledger_sig(r["notrace"])
    assert r["monitor"].best.id == r["nomonitor"].best.id == r["notrace"].best.id
    mons = [e for e in load_trace(tmp / "monitor") if e["kind"] == "monitor"]
    assert mons and all(set(e["data"]["sealed"]) == sealed for e in mons)
    assert not any(e["kind"] == "monitor" for e in load_trace(tmp / "nomonitor"))
    assert not (tmp / "notrace" / "trace.jsonl").exists()


def test_mh_monitor_is_write_only(mh_runs):
    tmp, r = mh_runs
    _assert_monitor_write_only(tmp, r, {"ood"})          # test is left to finalize, which runs it once
    fin = [x.meta["final"]["splits"]["test"]["results"] for x in r.values()]
    assert fin[0] == fin[1] == fin[2]


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
        if e["kind"] == "proposal" and d["candidate"] in led:     # the recorded diff is the actual one
            base = store.artifact(d["parent"])
            assert d["diff"][:5000] == base.diff(store.artifact(d["candidate"]))[:5000]
        if e["kind"] == "eval":
            assert d["summary"]["split"] == "evolve"
        if e["kind"] == "decision":
            pts = [(n, s["score"], s["context_cost"]) for n in store.names() if (s := store.scores(n))
                   and store.meta(n).get("iteration", 0) <= e["round"]]
            front = {p[0] for p in pareto_frontier(pts)}
            for n, row in d["per_candidate"].items():
                if "on_frontier" in row:
                    assert row["on_frontier"] == (n in front)
    assert inspect(tmp / "monitor").endswith("TRACE.md")


def test_solpi_monitor_is_write_only(sp_runs):
    tmp, r = sp_runs
    _assert_monitor_write_only(tmp, r, {"holdout", "ood"})
    keys = ("chosen", "frozen", "heldout_passed", "survivors")
    rounds = [{k: x.meta["rounds"][0][k] for k in keys} for x in r.values()]
    assert rounds[0] == rounds[1] == rounds[2]


def test_solpi_trace_gate_arithmetic_and_firewall(sp_runs):
    tmp, r = sp_runs
    ev = load_trace(tmp / "monitor")
    spec = GateSpec(mode="aggregate")
    n_gate = 0
    for e in ev:
        d = e["data"]
        if e["kind"] == "gate" and "capability" in d["math"]:
            n_gate += 1
            caps = d["math"]["capability"]
            assert set(caps) == {m for m, _ in spec.capability}
            ok_cap = all(caps[m]["cand"] >= caps[m]["base"] * (1 - tol) - 1e-12 for m, tol in spec.capability)
            eff = d["math"]["efficiency"]
            ok_eff = any((v["base"] - v["cand"]) / v["base"] > spec.min_gain for v in eff.values() if v["base"] > 0)
            assert d["accept"] == (ok_cap and ok_eff)
        if e["kind"] == "eval" and "summary" in d:                 # lineages never evaluate a sealed split
            assert d["summary"]["split"] not in ("holdout", "ood", "test")
    assert n_gate > 0
    rnd = r["monitor"].meta["rounds"][0]
    fw = [e for e in ev if e["kind"] == "gate" and "firewall" in e["data"].get("stage", "")]
    assert {e["data"]["candidate"]: e["data"]["accept"] for e in fw} == rnd["heldout_passed"]
    sink = [json.loads(l) for l in (tmp / "monitor" / "firewall_r1" / "heldout.jsonl").read_text().splitlines()]
    assert {s["candidate"]: s["passed"] for s in sink} == rnd["heldout_passed"]
    assert inspect(tmp / "monitor").endswith("TRACE.md")


def test_rewrite_proposer_maps_src_layout_to_harness_root():
    """``src/<path>`` maps to ``<path>`` unless the base has a ``src/`` directory."""
    files = {"agents/new1/src/harness.py": "new\n"}
    header = {"candidates": [{"name": "new1", "base_system": "seed", "hypothesis": "h"}]}
    [c] = _collect(files, header, 2, {"seed": Artifact({"harness.py": "old\n", "prompts/task.md": "t\n"})}, set(), 1)
    assert c.artifact.files == {"harness.py": "new\n", "prompts/task.md": "t\n"}
    [c2] = _collect(files, header, 2, {"seed": Artifact({"src/harness.py": "old\n"})}, set(), 1)
    assert c2.artifact.files == {"src/harness.py": "new\n"}


def test_runtime_api_doc_states_preimports_and_call_id():
    assert '"/.solpi/' in RUNTIME_API_DOC and "KEYS ARE" in RUNTIME_API_DOC
    assert "PRE-IMPORTED" in RUNTIME_API_DOC and "event.call" in RUNTIME_API_DOC and ".id" in RUNTIME_API_DOC


def test_llm_reviewer_sees_the_runtime_api_for_solpi_harnesses():
    class Spy(LLM):
        def __init__(self):
            super().__init__()
            self.prompts = []

        def complete(self, prompt, **kw):
            self.prompts.append(prompt)
            return LLMResponse(text='{"verdict": "pass", "reasons": []}', usage=Usage(), model="spy")

    spy = Spy()
    base = Artifact({"harness.json": '{"extensions": {}}'})
    cand = Artifact({"harness.json": '{"extensions": {"x": {}}}',
                     "extensions/x.py": "class MECHANISM(Extension): pass\n"})
    ok, _ = LLMReviewer(spy).review(Idea("L1", "C", "t"), base, cand)
    assert ok and RUNTIME_API_DOC in spy.prompts[0]


class _RepairingProposer:
    """The first implementation breaches the contract; ``fix`` repairs it when the reviewer says why."""

    def __init__(self):
        self.fix_errors = []

    def propose(self, idea, base, evidence, history):
        if history:
            return MechanismProposal(None, error="single variant", meta={"exhausted": True})
        cfg = json.loads(base["config.json"])
        cfg.update(verbosity=cfg["verbosity"] - 1, note="holdout")      # contract breach the reviewer catches
        return MechanismProposal(base.with_files({"config.json": json.dumps(cfg, sort_keys=True)}),
                                 change="trim1 (with a held-out reference)")

    def fix(self, idea, prop, error):
        self.fix_errors.append(error)
        if "reviewer" not in error:
            return MechanismProposal(None, error=error, meta={"exhausted": True})
        cfg = json.loads(prop.artifact["config.json"])
        cfg.pop("note")
        return MechanismProposal(prop.artifact.with_files({"config.json": json.dumps(cfg, sort_keys=True)}),
                                 change="trim1")


def test_review_rejection_routes_back_to_implementation(tmp_path):
    """Reviewer -> Implementation: an objection goes to the implementer before the iteration ends."""
    dom = verbosity_domain()
    prop = _RepairingProposer()
    res = sp_run(dom, VERBOSITY_SEED, ideas=VERBOSITY_IDEAS[:1], proposer=prop,
                 config=SPConfig(n_lineages=1, max_iters=1, review_max=2), out_dir=tmp_path)
    r = res.meta["rounds"][0]
    assert r["survivor_ideas"] == ["G1"] and json.loads(res.best["config.json"]) == {"verbosity": 3}
    assert len(prop.fix_errors) == 1 and "holdout" in prop.fix_errors[0]
    it = r["lineages"][0]["iterations"]
    assert len(it) == 1 and it[0]["outcome"] == "frozen" and len(it[0]["review_repairs"]) == 1
    crit = [e["data"] for e in load_trace(tmp_path) if e["kind"] == "critic"]
    assert [c["accept"] for c in crit] == [False, True] and crit[1]["candidate"] == "G1.0r1"
    prop0 = _RepairingProposer()                                         # review_max=0: a rejection ends it
    res0 = sp_run(dom, VERBOSITY_SEED, ideas=VERBOSITY_IDEAS[:1], proposer=prop0,
                  config=SPConfig(n_lineages=1, max_iters=1, review_max=0), out_dir=tmp_path / "r0")
    assert res0.meta["rounds"][0]["survivor_ideas"] == [] and prop0.fix_errors == []


def test_ralph_loop_sums_repair_usage_and_stops_when_exhausted():
    class Repairs:
        n = 0

        def fix(self, idea, prop, error):
            self.n += 1
            if self.n == 1:
                return MechanismProposal(Artifact({"a": "2"}), usage=Usage(1, 10, 10, 0.5))
            return MechanismProposal(Artifact({"a": "ok"}), usage=Usage(1, 10, 10, 0.25))

    first = MechanismProposal(Artifact({"a": "1"}), usage=Usage(1, 10, 10, 1.0))
    out, errs = implement(Repairs(), Idea("X", "C", "t"), first, lambda a: None if a["a"] == "ok" else "bad", 3)
    assert out.artifact["a"] == "ok" and len(errs) == 2 and abs(out.usage.cost_usd - 1.75) < 1e-12
    assert out.usage.calls == 3

    class Exhausted:
        calls = 0

        def fix(self, idea, prop, error):
            self.calls += 1
            return MechanismProposal(None, error="x", meta={"exhausted": True})

    q = Exhausted()
    out, errs = implement(q, Idea("X", "C", "t"),
                          MechanismProposal(None, error="variant grid exhausted", meta={"exhausted": True}),
                          lambda a: None, 3)
    assert q.calls == 0 and out.error == "variant grid exhausted"


def test_forked_smoke_usage_reaches_the_parent_meter():
    """A forked smoke run ships its model usage back to the parent meter."""

    class Paid(LLM):
        def complete(self, prompt, **kw):
            u = Usage(1, 100, 20, 0.01)
            self.meter.add(kw.get("role", "default"), u)
            return LLMResponse(text="ok", usage=u, model="paid")

    class Dom:
        def smoke(self, artifact, llm):
            llm.complete("hi", role="task")

    llm = Paid()
    ok, msg = InterfaceValidator(timeout_s=30, isolate=True).validate(Dom(), Artifact({"h.py": "x = 1\n"}), llm)
    assert ok, msg
    t = llm.meter.total()
    assert t.calls == 1 and abs(t.cost_usd - 0.01) < 1e-12 and llm.meter.by_role["task"].input_tokens == 100
