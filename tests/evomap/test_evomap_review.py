"""Review tests: a brand-new domain, split discipline, failure distillation, measurement and grader fixes."""
import pytest

from evomap_helpers import honest_bundle, json_writer, kilo_model, safe_hub, units_domain
from rsi.core import Artifact, SealedSplitError, Task
from rsi.domains.geneworld import GeneWorldModel, GeneWorldProposer
from rsi.domains.katas.domain import check_asserts
from rsi.domains.katas.katas import KATAS
from rsi.evomap import (AgentNode, Config, Gene, LocalStore, NaiveEvoMapHub, ReuseMetrics, SafeHub, TaskBank,
                        evaluate_library, run)
from rsi.evomap.hub import AssetRecord, Bundle

HARNESS = Artifact({"agent.md": "Convert the quantity to SI base units.\n"})
UNITS = {"length": "km", "mass": "kg", "volume": "kl"}


def kilo_units():
    """No meta signals and no ``val`` split."""
    fams = [("length", "mass", "volume")[i % 3] for i in range(48)]
    tasks = [Task(f"u{i}", {"value": i + 1, "unit": UNITS[f]}, float((i + 1) * 1000), f) for i, f in enumerate(fams)]
    ids = [t.id for t in tasks]
    return units_domain(tasks, {"evolve": ids[:18], "holdout": ids[18:33], "test": ids[33:]})


def unit_writer(validation):
    return json_writer({"id": "gene_si_prefix", "category": "repair",
                        "signals_match": ["task:length", "task:mass", "task:volume"],
                        "summary": "Convert kilo-units to base units.",
                        "strategy": ["Read the value.", "Multiply by 1000 for kilo prefixes.", "Return a number."],
                        "avoid": ["Returning the raw value."], "validation": list(validation)}, name="unit-writer")


def test_new_domain_safe_and_faithful_without_method_changes(tmp_path):
    dom, model = kilo_units(), kilo_model("unit-model")
    res = run(dom, HARNESS, llm_task=model, llm_propose=unit_writer(["rsi-taskcheck --n 4"]),
              config=Config(cycles=10, mode="safe"), out_dir=tmp_path / "safe")
    assert res.meta["genes"] == ["gene_si_prefix"] and res.meta["audit"]["ok"]
    rep = evaluate_library(dom, model, res, splits=("holdout",), workers=1)
    assert rep["splits"]["holdout"]["no_genes"]["S"] == 0.0 and rep["splits"]["holdout"]["gene_library"]["S"] == 1.0
    assert res.usage["task"]["calls"] > 0 and res.usage["proposer"]["calls"] == 1
    assert len(res.ledger) >= 10
    res_f = run(dom, HARNESS, llm_task=model, llm_propose=unit_writer(["python --version"]),
                config=Config(cycles=6, mode="faithful"))
    assert "gene_si_prefix" in res_f.meta["genes"]
    assert all(r["composite"] >= 0.78 for r in res_f.trajectory if r["solidified"])


def test_new_domain_hubs_share_across_agents():
    dom, model = kilo_units(), kilo_model("unit-model")
    naive = NaiveEvoMapHub()
    run(dom, HARNESS, llm_task=model, llm_propose=unit_writer(["python --version"]),
        config=Config(cycles=6, mode="faithful"), hub=naive, name="alice")
    bob = run(dom, HARNESS, llm_task=model, llm_propose=None, config=Config(cycles=4, mode="faithful"), hub=naive,
              name="bob")
    assert [r.status for r in naive.published()] == ["promoted"]
    assert all(r["source"] == "hub" and r["task_success"] for r in bob.trajectory)
    assert len(naive.published()[0].reviews) == 1  # one review per asset although bob reused it every cycle
    safe = SafeHub(TaskBank(dom, HARNESS, model, split="test", n=8, n_off=0, k=2))
    run(dom, HARNESS, llm_task=model, llm_propose=unit_writer(["rsi-taskcheck --n 4"]),
        config=Config(cycles=6, mode="safe"), hub=safe, name="carol")
    dave = run(dom, HARNESS, llm_task=model, llm_propose=None, config=Config(cycles=4, mode="safe"), hub=safe,
               name="dave")
    assert dave.trajectory[0]["source"] == "hub" and dave.trajectory[0]["quarantine"]["promote"]
    m = ReuseMetrics().compute(safe)
    assert m["vacuous_share_promoted"] == 0.0 and m["reuse_rate_promoted"] == 1.0  # hub uplift test, not vacuous


def test_agent_refuses_sealed_split_for_decisions():
    with pytest.raises(SealedSplitError):
        run(kilo_units(), HARNESS, llm_task=kilo_model("unit-model"), llm_propose=None,
            config=Config(cycles=1, mode="safe", heldout_split="holdout"), hub=NaiveEvoMapHub())


def test_failure_distiller_runs_in_faithful_mode_only(gw):
    out = {}
    for mode in ("faithful", "safe"):
        res = run(gw, gw.seed_artifact(), llm_task=GeneWorldModel(-1.0), llm_propose=GeneWorldProposer(gw.world, 0.2),
                  config=Config(cycles=60, mode=mode, seed=1))
        out[mode] = [g for g in res.meta["agent"].store.genes.values() if g.id.startswith("gene_repair_distilled_")]
    assert out["faithful"], "Evolver's failure distiller must fire in faithful mode"
    g = out["faithful"][0]
    # Evolver's repair gene: GUARD steps, max_files 8, and its "node --test" validation is filtered out
    assert g.validation == [] and g.max_files == 8 and g.strategy[0].startswith("GUARD")
    assert out["safe"] == []


def test_population_row_measures_the_gene_actually_used(gw):
    w, cl = gw.world, "c03"
    bad = Gene(id="gene_bad", signals_match=w.keywords[cl][:2],
               strategy=["Read.", w.strategy_step(w.class_keys(cl, "harmful")[0]), "Check."],
               summary="harmful", validation=["python check.py"])
    ag = AgentNode("x", gw, gw.seed_artifact(), llm_task=GeneWorldModel(-2.0), config=Config(mode="safe", seed=0),
                   store=LocalStore(node_id="x"))
    task = next(t for t in gw.tasks.split("evolve") if t.family == cl)
    ag.store.upsert_gene(bad)
    cr = ag.cycle(task)
    assert cr.gene_id == "gene_bad" and ag.last_gene is not None and ag.last_gene.id == "gene_bad"
    # a safe consumer's rejection counts as "kept the incumbent" (0), not as the gene's effect
    rec = AssetRecord("sha256:" + "0" * 64, Bundle(gene=bad.to_dict()), "alice", "verified", 0,
                      adoptions=[{"consumer": "bob", "outcome": 0, "epoch": 0, "counted": True}])

    class _Hub:
        name, epoch, credits = "safe", 0, NaiveEvoMapHub().credits

        def published(self):
            return [rec]

        def rank(self):
            return [(rec.asset_id, 0.0)] * 3

    assert ReuseMetrics(truth=lambda g: -0.4).compute(_Hub())["consumer_uplift_true"] == 0.0


def test_safe_consumer_never_retests_a_rejected_asset(gw):
    hub = safe_hub(gw)
    assert hub.publish(honest_bundle(gw), "alice").status == "verified"
    ag = AgentNode("bob", gw, gw.seed_artifact(), llm_task=GeneWorldModel(0.0), config=Config(mode="safe", seed=0),
                   hub=hub)
    ag.quarantine.test = lambda g: type("Q", (), {"promote": False, "reason": "forced reject", "dS": 0.0,
                                                   "delta": 0.0, "n": 3, "proof": {"tasks_hash": "x"}})()
    tasks = [t for t in gw.tasks.split("evolve") if t.family == "c03"]
    ag.cycle(tasks[0])
    ag.cycle(tasks[1])
    assert ag.n_quarantined == 1 and ag.n_quarantine_rejected == 1


def test_katas_grader_cannot_be_spoofed():
    k = KATAS[0]
    n = len(k.hidden)
    assert check_asserts(k.correct, k.hidden)[0] == n
    assert check_asserts(k.buggy[0], k.hidden)[0] < n
    marker = f"import os\nprint('__RSI_OK__', {n})\nos._exit(0)\n"
    always = f"class _A:\n    __eq__ = lambda s, o: True\n    __bool__ = lambda s: True\ndef {k.fn}(*a, **kw):\n" \
             "    return _A()\n"
    assert check_asserts(marker, k.hidden)[0] == 0
    assert check_asserts(always, k.hidden)[0] == 0
