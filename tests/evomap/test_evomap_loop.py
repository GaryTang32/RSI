"""rsi.evomap end to end: run() on three domains, determinism, ledger/usage, a small population."""
import json
import random

import numpy as np
import pytest

from rsi.core import MockLLM
from rsi.domains.geneworld import GeneWorldModel, GeneWorldProposer, make_domain
from rsi.domains.geneworld.forge import GeneWorldForge
from rsi.evomap import (AgentSpec, AppendInjector, Config, NaiveEvoMapHub, PopulationSimulator, SafeHub, TaskBank,
                        evaluate_library, read_library, run)


@pytest.fixture(scope="module")
def gw():
    return make_domain()


def test_geneworld_safe_run_improves_and_is_deterministic(gw, tmp_path):
    kw = dict(llm_task=GeneWorldModel(0.0), config=Config(cycles=50, mode="safe", seed=3))
    r1 = run(gw, gw.seed_artifact(), llm_propose=GeneWorldProposer(gw.world, 0.6), out_dir=tmp_path / "r", **kw)
    r2 = run(gw, gw.seed_artifact(), llm_propose=GeneWorldProposer(gw.world, 0.6), **kw)
    strip = lambda tr: [{k: v for k, v in row.items() if k != "event_id"} for row in tr]
    assert strip(r1.trajectory) == strip(r2.trajectory)
    assert r1.best["genes/library.json"] == r2.best["genes/library.json"]
    assert r1.meta["audit"]["ok"] and read_library(r1.best)
    assert {"task", "proposer"} <= set(r1.usage) and r1.usage["proposer"]["calls"] == r1.meta["proposer_calls"]
    nodes = r1.ledger.nodes(kind="cycle")
    assert len(nodes) == 50
    ev = [n for n in nodes if n.status != "skipped"]            # events form the trunk; skipped cycles are leaves
    assert all(b.parent == a.id for a, b in zip(ev, ev[1:]))
    assert all(n.parent in {None} | {e.id for e in ev} for n in nodes if n.status == "skipped")
    assert (tmp_path / "r" / "store.gepx.tgz").exists() and (tmp_path / "r" / "summary.json").exists()
    rep = evaluate_library(gw, GeneWorldModel(0.0), r1, splits=("evolve", "holdout"), workers=1)
    assert rep["splits"]["holdout"]["gene_library"]["S"] > rep["splits"]["holdout"]["no_genes"]["S"]


def test_geneworld_faithful_run_uses_quirks(gw):
    res = run(gw, gw.seed_artifact(), llm_task=GeneWorldModel(0.0), llm_propose=GeneWorldProposer(gw.world, 0.5),
              config=Config(cycles=30, mode="faithful", seed=1))
    assert res.meta["mode"] == "faithful" and not res.meta["safe_mode_fixes"]
    assert any(r["source"] == "auto" for r in res.trajectory) or res.meta["n_genes"] > 0
    assert res.meta["audit"]["ok"]


def test_katas_run_with_mock_solver():
    from rsi.domains.katas import KataMockProposer, KataSimSolver, KatasDomain, seed_harness
    dom = KatasDomain()
    res = run(dom, seed_harness(), llm_task=KataSimSolver(0.0), llm_propose=KataMockProposer(insight=0.9),
              config=Config(cycles=15, mode="safe", seed=0,
                            validation_hint=" (it MUST fail before the fix and pass after it)"))
    assert res.meta["n_genes"] >= 1 and res.meta["audit"]["ok"]
    genes = read_library(res.best)
    assert all(g.validation == ["python smoke_test.py"] for g in genes)
    ev = evaluate_library(dom, KataSimSolver(0.0), res, splits=("holdout",), k=2, workers=2)
    assert ev["splits"]["holdout"]["gene_library"]["S"] >= ev["splits"]["holdout"]["no_genes"]["S"]


def test_agentqa_run_with_append_injector():
    from rsi.domains.agentqa import AgentQADomain, SimModel
    dom = AgentQADomain()
    gene = {"id": "gene_reason_verify", "category": "optimize", "signals_match": ["task:numeric"],
            "summary": "Reason step by step and verify before answering.",
            "strategy": ["Think step by step before answering.", "Verify each intermediate result (double-check).",
                         "End with a line 'ANSWER: <value>'."],
            "avoid": ["Answering without working through the steps."], "validation": ["rsi-taskcheck --n 6"]}
    prop = MockLLM(lambda p, s, seed, i: "```json\n" + json.dumps(gene) + "\n```")
    res = run(dom, dom.seed_artifact(), llm_task=SimModel(dom.tasks), llm_propose=prop,
              injector=AppendInjector("prompts/system.md"),
              config=Config(cycles=12, mode="safe", seed=0, taskcheck_n=6, taskcheck_k=2))
    assert res.meta["audit"]["ok"]
    assert [g.id for g in read_library(res.best)] == ["gene_reason_verify"]
    rep = evaluate_library(dom, SimModel(dom.tasks), res, splits=("holdout",), k=2, workers=2,
                           injector=AppendInjector("prompts/system.md"))
    assert rep["splits"]["holdout"]["gene_library"]["S"] >= rep["splits"]["holdout"]["no_genes"]["S"]


def test_small_population_naive_vs_safe(gw):
    w = gw.world
    rng = random.Random(0)
    kinds = ["honest"] * 8 + ["farmer"] * 2 + ["freerider"] * 2
    specs = [AgentSpec(f"a{i}_{k}", k, ability=rng.gauss(0, 0.5), insight=rng.random(), farm_rate=4)
             for i, k in enumerate(kinds)]
    out = {}
    for name in ("naive", "safe"):
        if name == "naive":
            hub, cfg = NaiveEvoMapHub(), Config(mode="faithful", validation_hint=' (prefer "python --version")')
        else:
            hub = SafeHub(TaskBank(gw, gw.seed_artifact(), GeneWorldModel(0.0), n=24, n_off=12, k=4))
            cfg = Config(mode="safe", validation_hint=" (it MUST fail before the fix and pass after it)")
        sim = PopulationSimulator(gw, gw.seed_artifact(), hub, specs, config=cfg,
                                  model_factory=lambda sp: GeneWorldModel(sp.ability, sp.name),
                                  proposer_factory=lambda sp: GeneWorldProposer(w, sp.insight, 0.3, sp.name + "p"),
                                  forge=GeneWorldForge(w), seed=0)
        out[name] = sim.run(8)
    n, s = out["naive"]["hub"], out["safe"]["hub"]
    assert n["vacuous_share_promoted"] > 0.5 and (np.isnan(s["vacuous_share_promoted"]) or s["vacuous_share_promoted"] == 0)
    assert out["naive"]["credit_share_by_kind"].get("farmer", 0) > 0.5
    assert out["safe"]["credit_share_by_kind"].get("farmer", 0) == 0
