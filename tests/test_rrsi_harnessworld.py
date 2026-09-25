"""HarnessWorld: determinism, ground truth, leak mechanics, split discipline, mocks."""
import pytest

from rsi.core import Evaluator, SealedSplitError
from rsi.domains.harnessworld import (KINDS, STRUCTURAL, WEAK, HarnessWorldMockLLM, ProposerProfile, make_domain)
from rsi.rrsi import Config, Taxonomy
from rsi.rrsi.propose import RRSIRewriteEditor, Proposer

SMALL = dict(n_evolve=30, n_holdout=30, n_ood_per_family=8)


def test_world_is_deterministic_and_complete():
    a, b = make_domain(seed=5, **SMALL), make_domain(seed=5, **SMALL)
    assert a.seed_artifact().id == b.seed_artifact().id
    assert {m.kind for m in a.world.catalog.values()} == set(KINDS)
    assert make_domain(seed=6, **SMALL).seed_artifact().id != a.seed_artifact().id
    assert set(a.tasks.splits) >= {"evolve", "holdout", "ood", "smoke"}
    with pytest.raises(SealedSplitError):
        a.tasks.split("holdout")


def test_expected_matches_sampled_mean():
    dom = make_domain(seed=1, **SMALL)
    art = dom.seed_artifact()
    ev = Evaluator(dom, None, workers=1).evaluate(art, "evolve", k=40)
    exp = dom.expected(art, "evolve")
    assert ev.score == pytest.approx(exp["S"], abs=0.02)
    assert ev.cost == pytest.approx(exp["C"], rel=0.05)


def test_leak_helps_evolve_only_and_needs_payload():
    dom = make_domain(seed=1, **SMALL)
    base = dom.seed_artifact()
    leak = next(m for m in dom.world.catalog.values() if m.kind == "leak")
    with_leak = base.with_files({leak.path: leak.file_text()})
    assert dom.expected(with_leak, "evolve")["S"] > dom.expected(base, "evolve")["S"] + 0.02
    assert dom.expected(with_leak, "holdout")["S"] < dom.expected(base, "holdout")["S"]
    stripped = base.with_files({leak.path: leak.file_text().replace(leak.payload + "\n", "")})
    assert dom.expected(stripped, "evolve")["S"] < dom.expected(with_leak, "evolve")["S"] - 0.02
    assert any(t in with_leak[leak.path] for t in dom.leakage_terms("evolve"))


def test_decay_and_costs_and_weak_policy():
    dom = make_domain(seed=2, **SMALL)
    base = dom.seed_artifact()
    dec = next(m for m in dom.world.catalog.values() if m.kind == "decaying")
    sup = dom.world.catalog[dec.superseded_by[0]]
    h1 = base.with_files({dec.path: dec.file_text()})
    h2 = h1.with_files({sup.path: sup.file_text()})
    h3 = base.with_files({sup.path: sup.file_text()})
    # once superseded, the decaying mechanism only costs tokens: removing it does not lower E[S]
    assert dom.expected(h2, "evolve")["S"] <= dom.expected(h3, "evolve")["S"] + 1e-9
    assert dom.expected(h2, "evolve")["C"] > dom.expected(h3, "evolve")["C"]
    weak = dom.with_policy(WEAK)
    assert weak.expected(base, "ood")["S"] < dom.expected(base, "ood")["S"]
    bad = base.with_files({"control/x.py": "def (:\n"})
    assert dom.run(bad, dom.tasks.get(dom.tasks.splits["evolve"][0])).error.startswith("SyntaxError")
    assert dom.smoke(base.with_files({"harness.md": None})) is not None


def test_taxonomy_on_harnessworld_paths():
    dom = make_domain(seed=0, **SMALL)
    tax = Taxonomy.from_domain(dom)
    assert tax.K_str == list(STRUCTURAL)
    base = dom.seed_artifact()
    for m in [m for m in dom.world.catalog.values() if m.id not in dom.world.seed_ids][:40]:
        d = base.diff(base.with_files({m.path: m.file_text()}))
        assert tax.normalize(m.component, d) == m.component
        assert tax.classify(d) == m.component


def test_mock_proposer_respects_budget_reserved_and_history():
    dom = make_domain(seed=0, **SMALL)
    llm = HarnessWorldMockLLM(dom.world, proposer=ProposerProfile(fill_budget_p=1.0))
    tax = Taxonomy.from_domain(dom)
    prop = Proposer(RRSIRewriteEditor(llm), tax, Config())
    base = dom.seed_artifact()
    untried = ["skill", "memory"]
    out = prop.propose(base, directives={"b_t": 3, "reserved_slot": True, "untried": untried,
                                         "trace_task_ids": dom.tasks.splits["evolve"][:5]},
                       budget=3, reserved=True, explore={"untried": untried}, seed=4)
    assert out["status"] == "done" and len(out["edits"]) == 3
    assert any(e["component"] in untried for e in out["edits"])
    mids = [e["mechanism_id"] for e in out["edits"]]
    rows = [{"t": 0, "variant": "A", "edit_id": "C1", "component": "prompt", "hypothesis": f"[{m}] x",
             "outcome": "REJECTED", "delta_S": -0.1} for m in mids]
    llm2 = HarnessWorldMockLLM(dom.world, proposer=ProposerProfile(fill_budget_p=1.0, history_compliance=1.0))
    out2 = Proposer(RRSIRewriteEditor(llm2), tax, Config()).propose(
        base, directives={"b_t": 3, "trace_task_ids": ["hw-e-000"]}, budget=3, history_rows=rows, seed=4)
    assert not set(mids) & {e["mechanism_id"] for e in out2["edits"]}
