"""SoL-Pi research protocol + AgentWorld tests (offline, deterministic)."""
import json

import pytest

from rsi.core import Artifact, Evaluator, SealedSplitError
from rsi.core.gates import Scored
from rsi.domains.agentworld import MockAgentLLM, make_domain
from rsi.solpi import (AGENTWORLD_IDEAS, Config, DualGate, GateSpec, HoldoutFirewall, LibraryProposer, Lineage,
                       Metrics, SmokeReviewer, compose, merge3, metrics_from_eval, nondominated, run)


@pytest.fixture(scope="module")
def dom():
    return make_domain(seed=0, n_train=3, n_accept=3, n_final=3, n_test=1)


def M(score, tokens, cost, fam=None):
    agg = {"score": score, "tokens": tokens, "cost": cost, "steps": 1.0, "eta": cost / score}
    return Metrics(agg, fam or {"f": dict(agg)}, 1)


# ------------------------------------------------------------------ gate
def test_dual_gate_modes():
    g = DualGate(GateSpec(capability=(("score", 0.02),), efficiency=("tokens", "cost"), min_gain=0.02))
    base = M(1.0, 100, 1.0)
    assert g.accept(base, M(0.99, 80, 1.0)).accept                      # within 2% and cheaper
    assert not g.accept(base, M(0.95, 50, 0.5)).accept                  # do-less: capability floor
    assert not g.accept(base, M(1.0, 99, 0.99)).accept                  # gain below min_gain
    naive = DualGate(GateSpec(mode="efficiency_only"))
    assert naive.accept(base, M(0.5, 50, 0.5)).accept
    fam_b = {"a": {"score": 1.0, "tokens": 100, "cost": 1.0}, "b": {"score": 1.0, "tokens": 100, "cost": 1.0}}
    fam_c = {"a": {"score": 1.0, "tokens": 50, "cost": 0.5}, "b": {"score": 0.9, "tokens": 100, "cost": 1.0}}
    b2, c2 = M(1.0, 100, 1.0, fam_b), M(0.95 + 0.04, 75, 0.75, fam_c)
    assert DualGate(GateSpec(mode="aggregate", capability=(("score", 0.02),))).accept(b2, c2).accept
    pf = DualGate(GateSpec(mode="per_family")).accept(b2, c2)
    assert not pf.accept and "some family" in pf.reason                  # survives in aggregate, not everywhere
    assert DualGate(GateSpec()).digest == DualGate(GateSpec()).digest
    # aggregate + absolute tolerance agrees with rsi.core's DualGate
    ga = DualGate(GateSpec(tolerance_kind="absolute"))
    for c in (M(0.99, 80, 0.8), M(0.9, 50, 0.5), M(1.0, 99.5, 0.995)):
        core = ga.as_core_gate().check(DualGate.scored(c), DualGate.scored(base), None)
        assert ga.accept(base, c).accept == core.accept


def test_nondominated():
    a, b, c = M(1.0, 50, 0.5), M(1.0, 60, 0.6), M(0.9, 40, 0.4)
    assert nondominated([("a", a), ("b", b), ("c", c)]) == ["a", "c"]


# ------------------------------------------------------------------ firewall + lineage isolation
def test_firewall_is_one_way_and_lineages_cannot_read_holdout(dom, tmp_path):
    llm = MockAgentLLM("A")
    ev = Evaluator(dom, llm)
    with pytest.raises(SealedSplitError):
        ev.evaluate(dom.seed_artifact(), "holdout")
    gate = DualGate(GateSpec())
    fw = HoldoutFirewall(dom, llm, gate, dom.seed_artifact(), sink_dir=tmp_path)
    cand = dom.harness("action_fusion")
    verdict = fw.evaluate_frozen("af", cand)
    assert isinstance(verdict, bool)
    with pytest.raises(RuntimeError):
        fw.evaluate_frozen("af", cand)                                  # exactly once
    assert (tmp_path / "heldout.jsonl").exists() and len(fw.final_report()) == 1
    bm = metrics_from_eval(ev.evaluate(dom.seed_artifact(), "evolve"))
    lin = Lineage(AGENTWORLD_IDEAS[1], evaluator=ev, gate=gate, proposer=LibraryProposer(),
                  reviewer=SmokeReviewer(dom, llm), base=dom.seed_artifact(), base_metrics=bm)
    assert not any(isinstance(v, HoldoutFirewall) for v in vars(lin).values())
    res = lin.run()
    assert res.frozen is not None and all(it["stage"] != "heldout" for it in res.iterations)


def test_reviewer_rejects_references_to_heldout(dom):
    rv = SmokeReviewer(dom, MockAgentLLM("A"))
    base = dom.seed_artifact()
    bad = base.with_files({"system_prompt.md": base["system_prompt.md"] + "\nFor datalookup tasks, grep first."})
    ok, why = rv.review(AGENTWORLD_IDEAS[0], base, bad)
    assert not ok and "held-out" in why


# ------------------------------------------------------------------ composition
def test_merge3_and_compose():
    base = "a\nb\nc\nd\n"
    assert merge3(base, "A\nb\nc\nd\n", "a\nb\nc\nD\n") == "A\nb\nc\nD\n"
    assert merge3(base, "X\nb\nc\nd\n", "Y\nb\nc\nd\n") is None
    b = Artifact({"harness.json": json.dumps({"extensions": {}}), "p.md": base})
    c1 = b.with_files({"harness.json": json.dumps({"extensions": {"action_fusion": {}}})})
    c2 = b.with_files({"harness.json": json.dumps({"extensions": {"observation_pack": {"full_sends": 2}}}),
                       "p.md": "a\nb\nc\nD\n"})
    comp, conflicts = compose(b, [c1, c2])
    assert json.loads(comp["harness.json"])["extensions"] == {"action_fusion": {},
                                                              "observation_pack": {"full_sends": 2}}
    assert comp["p.md"] == "a\nb\nc\nD\n" and conflicts == []


# ------------------------------------------------------------------ AgentWorld
def test_agentworld_deterministic_and_mechanisms_save_tokens(dom):
    llm = MockAgentLLM("A")
    t = dom.tasks.split("evolve")[0]
    r1 = dom.run(dom.seed_artifact(), t, seed=0, llm=llm)
    r2 = dom.run(dom.seed_artifact(), t, seed=0, llm=MockAgentLLM("A"))
    assert (r1.score, r1.tokens, r1.cost_usd) == (r2.score, r2.tokens, r2.cost_usd)
    ev = Evaluator(dom, llm)
    base = ev.evaluate(dom.seed_artifact(), "evolve")
    stack = ev.evaluate(dom.harness("action_fusion", "observation_pack", "evidence_preserving_reducer",
                                    "online_context_compact"), "evolve")
    assert stack.cost < 0.8 * base.cost and stack.score >= base.score - 0.05
    tr = next(iter(stack.trials.values()))[0]
    assert set(tr.meta["triggers"]) == {"action_fusion", "observation_pack", "evidence_preserving_reducer",
                                        "online_context_compact"}
    assert tr.meta["oracle"]["transitions"] > 0


def test_tricks_break_where_their_assumption_fails(dom):
    ev = Evaluator(dom, MockAgentLLM("A"), allow_sealed=True)
    base = ev.evaluate(dom.seed_artifact(), "ood").score
    tail = ev.evaluate(dom.harness("tail_trim"), "ood").score
    assert tail < base


# ------------------------------------------------------------------ end to end
def test_protocol_end_to_end_agentworld(tmp_path):
    d = make_domain(seed=0, n_train=3, n_accept=3, n_final=2, n_test=1)
    res = run(d, d.seed_artifact(), llm_task=MockAgentLLM("A"), config=Config(gate=GateSpec(mode="aggregate")),
              out_dir=tmp_path)
    r = res.meta["rounds"][0]
    kinds = {l["idea"]: l["kind"] for l in r["lineages"]}
    assert not any(kinds[i] == "do_less" for i in r["survivor_ideas"])
    assert "T3" not in r["survivor_ideas"]                 # tail trimming breaks head-evidence families
    assert {"P8", "D1"} <= set(r["survivor_ideas"])        # fusion + reducer survive everywhere
    assert r["composed_metrics"]["agg"]["cost"] < r["base_metrics"]["agg"]["cost"]
    assert (tmp_path / "rounds.json").exists() and len(res.ledger) > 0
    naive = run(d, d.seed_artifact(), llm_task=MockAgentLLM("A"),
                config=Config(gate=GateSpec(mode="efficiency_only"), firewall=False), out_dir=tmp_path / "n")
    nk = {l["idea"]: l["kind"] for l in naive.meta["rounds"][0]["lineages"]}
    assert any(nk[i] == "do_less" for i in naive.meta["rounds"][0]["frozen_ideas"])


def test_protocol_runs_on_agentqa(tmp_path):
    from rsi.domains.agentqa import AgentQADomain, SimModel, make_suite
    from rsi.metaharness.mock import AgentQALibrary
    suite = make_suite(n_evolve=9, n_holdout=6, n_ood_per_family=2, practice_families=("numeric", "strings", "lists"))
    d = AgentQADomain(suite)
    lib = AgentQALibrary()
    seed = lib.render({"cot": True, "format": True}, d.seed_artifact())
    res = run(d, seed, llm_task=SimModel(suite), config=Config(gate=GateSpec(efficiency=("tokens",))),
              out_dir=tmp_path)
    r = res.meta["rounds"][0]
    assert "R2" not in r["survivor_ideas"]          # dropping reasoning lowers accuracy -> rejected
    assert r["lineages"] and res.best is not None


# ------------------------------------------------------------------ environments + LLM-written mechanisms
def test_every_environment_passes_the_validity_filter():
    from rsi.domains.agentworld import FAMILIES, validity_filter
    for fam, cls in FAMILIES.items():
        for i in range(5):
            assert validity_filter(cls(f"v-{fam}-{i}", seed=i, n_subtasks=3))


CODE_REPLY = '''```json
{"name": "tail_note", "params": {}, "change": "annotate long outputs"}
```
=== FILE: extensions/tail_note.py ===
```python
class MECHANISM:
    name = "tail_note"

    def register(self, rt):
        rt.on("tool_result", self.on_result)

    def on_result(self, event, rt):
        return None
```
The mechanism fails open because it never changes a result.
'''


def test_llm_mechanism_proposer_parses_fenced_code_and_duck_typed_mechanisms(dom):
    from rsi.core import MockLLM
    from rsi.solpi import LLMMechanismProposer, build_extensions
    from rsi.solpi.research import Idea
    prop = LLMMechanismProposer(MockLLM(lambda p, s, seed, i: CODE_REPLY)).propose(
        Idea("X1", "C", "annotate"), dom.seed_artifact(), {"n": 1}, [])
    assert prop.artifact is not None and "```" not in prop.artifact["extensions/tail_note.py"]
    exts = build_extensions(prop.artifact.files)
    assert [e.name for e in exts] == ["tail_note"]
    assert dom.smoke(prop.artifact, MockAgentLLM("A")) is None
