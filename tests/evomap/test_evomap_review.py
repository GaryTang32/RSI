"""Adversarial-review tests for rsi.evomap: genericity on a brand-new domain, split discipline, failure
distillation, measurement fixes (gene actually used, realized uplift, strategy-gene vacuity), consumer re-test
memory, one review per asset, and the locked katas grader."""
import json

import pytest

from rsi.core import Artifact, FunctionDomain, MockLLM, SealedSplitError, Task, TaskSuite
from rsi.evomap import (AgentNode, Config, Gene, LocalStore, NaiveEvoMapHub, ReuseMetrics, SafeHub, TaskBank,
                        evaluate_library, run)


# ----------------------------------------------------------------------------- a NEW tiny domain
def units_domain():
    """Unit conversion: no meta signals (only families), numeric outputs, no ``val`` split, FunctionDomain."""
    tasks = []
    for i in range(48):
        fam = ("length", "mass", "volume")[i % 3]
        tasks.append(Task(f"u{i}", {"value": i + 1, "unit": {"length": "km", "mass": "kg", "volume": "kl"}[fam]},
                          float((i + 1) * 1000), fam))
    ids = [t.id for t in tasks]
    suite = TaskSuite(tasks, {"evolve": ids[:18], "holdout": ids[18:33], "test": ids[33:]})

    def execute(artifact, task, seed, llm):
        guidance = "\n".join(artifact[p] for p in sorted(artifact))
        return float(llm.complete(json.dumps(task.input), system=guidance, seed=seed, role="task").text)

    def grade(task, out):
        return float(abs(out - task.target) < 1e-9), ""

    return FunctionDomain(suite, execute, grade, name="units")


def unit_model():
    """Frozen model: converts kilo-units only when its guidance says so."""
    def f(prompt, system, seed, i):
        x = json.loads(prompt)
        return str(x["value"] * (1000 if "multiply by 1000" in (system or "").lower() else 1))
    return MockLLM(f, name="unit-model")


def unit_writer(validation):
    g = {"id": "gene_si_prefix", "category": "repair", "signals_match": ["task:length", "task:mass", "task:volume"],
         "summary": "Convert kilo-units to base units.",
         "strategy": ["Read the value.", "Multiply by 1000 for kilo prefixes.", "Return a number."],
         "avoid": ["Returning the raw value."], "validation": list(validation)}
    return MockLLM(lambda p, s, seed, i: "```json\n" + json.dumps(g) + "\n```", name="unit-writer")


HARNESS = Artifact({"agent.md": "Convert the quantity to SI base units.\n"})


def test_new_domain_safe_and_faithful_without_method_changes(tmp_path):
    dom, model = units_domain(), unit_model()
    res = run(dom, HARNESS, llm_task=model, llm_propose=unit_writer(["rsi-taskcheck --n 4"]),
              config=Config(cycles=10, mode="safe"), out_dir=tmp_path / "safe")
    assert res.meta["genes"] == ["gene_si_prefix"] and res.meta["audit"]["ok"]
    rep = evaluate_library(dom, model, res, splits=("holdout",), workers=1)
    assert rep["splits"]["holdout"]["no_genes"]["S"] == 0.0 and rep["splits"]["holdout"]["gene_library"]["S"] == 1.0
    assert res.usage["task"]["calls"] > 0 and res.usage["proposer"]["calls"] == 1
    assert len(res.ledger) >= 10
    # faithful mode on the same domain: the Evolver-style "--version" validation passes the faithful runner
    res_f = run(dom, HARNESS, llm_task=model, llm_propose=unit_writer(["python --version"]),
                config=Config(cycles=6, mode="faithful"))
    assert "gene_si_prefix" in res_f.meta["genes"]
    assert all(r["composite"] >= 0.78 for r in res_f.trajectory if r["solidified"])


def test_new_domain_hubs_share_across_agents():
    dom, model = units_domain(), unit_model()
    naive = NaiveEvoMapHub()
    run(dom, HARNESS, llm_task=model, llm_propose=unit_writer(["python --version"]),
        config=Config(cycles=6, mode="faithful"), hub=naive, name="alice")
    bob = run(dom, HARNESS, llm_task=model, llm_propose=None, config=Config(cycles=4, mode="faithful"), hub=naive,
              name="bob")
    assert [r.status for r in naive.published()] == ["promoted"]
    assert all(r["source"] == "hub" and r["task_success"] for r in bob.trajectory)
    # bob reviewed the asset ONCE although he reused it in every cycle (§4.17)
    assert len(naive.published()[0].reviews) == 1
    safe = SafeHub(TaskBank(dom, HARNESS, model, split="test", n=8, n_off=0, k=2))
    run(dom, HARNESS, llm_task=model, llm_propose=unit_writer(["rsi-taskcheck --n 4"]),
        config=Config(cycles=6, mode="safe"), hub=safe, name="carol")
    dave = run(dom, HARNESS, llm_task=model, llm_propose=None, config=Config(cycles=4, mode="safe"), hub=safe,
               name="dave")
    assert dave.trajectory[0]["source"] == "hub" and dave.trajectory[0]["quarantine"]["promote"]
    m = ReuseMetrics().compute(safe)
    # a verified strategy gene (validated only by rsi-taskcheck, established by the hub's uplift test) is not vacuous
    assert m["vacuous_share_promoted"] == 0.0 and m["reuse_rate_promoted"] == 1.0


def test_agent_refuses_sealed_split_for_decisions():
    dom, model = units_domain(), unit_model()
    with pytest.raises(SealedSplitError):
        run(dom, HARNESS, llm_task=model, llm_propose=None,
            config=Config(cycles=1, mode="safe", heldout_split="holdout"), hub=NaiveEvoMapHub())


# ----------------------------------------------------------------------------- failure distillation
def test_failure_distiller_runs_in_faithful_mode_only():
    from rsi.domains.geneworld import GeneWorldModel, GeneWorldProposer, make_domain
    dom = make_domain()
    out = {}
    for mode in ("faithful", "safe"):
        res = run(dom, dom.seed_artifact(), llm_task=GeneWorldModel(-1.0), llm_propose=GeneWorldProposer(dom.world, 0.2),
                  config=Config(cycles=60, mode=mode, seed=1))
        ag = res.meta["agent"]
        out[mode] = [g for g in ag.store.genes.values() if g.id.startswith("gene_repair_distilled_")]
    assert out["faithful"], "Evolver's failure distiller must fire in faithful mode"
    g = out["faithful"][0]
    # Evolver's heuristic repair gene: GUARD steps, max_files 8, and its "node --test" validation is filtered out
    assert g.validation == [] and g.max_files == 8 and g.strategy[0].startswith("GUARD")
    assert out["safe"] == []


# ----------------------------------------------------------------------------- measurement fixes
def test_population_row_measures_the_gene_actually_used():
    from rsi.domains.geneworld import GeneWorldModel, make_domain
    dom = make_domain()
    w = dom.world
    cl = "c03"
    bad = Gene(id="gene_bad", signals_match=w.keywords[cl][:2],
               strategy=["Read.", w.strategy_step(w.class_keys(cl, "harmful")[0]), "Check."],
               summary="harmful", validation=["python check.py"])
    ag = AgentNode("x", dom, dom.seed_artifact(), llm_task=GeneWorldModel(-2.0), config=Config(mode="safe", seed=0),
                   store=LocalStore(node_id="x"))
    task = next(t for t in dom.tasks.split("evolve") if t.family == cl)
    ag.store.upsert_gene(bad)
    cr = ag.cycle(task)
    assert cr.gene_id == "gene_bad" and ag.last_gene is not None and ag.last_gene.id == "gene_bad"
    # the realized-uplift metric counts a safe consumer's rejection as "kept the incumbent" (0), not the gene's effect
    from rsi.evomap.hub import AssetRecord, Bundle
    rec = AssetRecord("sha256:" + "0" * 64, Bundle(gene=bad.to_dict()), "alice", "verified", 0,
                      adoptions=[{"consumer": "bob", "outcome": 0, "epoch": 0, "counted": True}])

    class _Hub:
        name, epoch, credits = "safe", 0, NaiveEvoMapHub().credits

        def published(self):
            return [rec]

        def rank(self):
            return [(rec.asset_id, 0.0)] * 3
    m = ReuseMetrics(truth=lambda g: -0.4).compute(_Hub())
    assert m["consumer_uplift_true"] == 0.0


def _honest_bundle(dom, cl="c03"):
    """A genuinely good gene with real, discriminative proof material."""
    from rsi.domains.geneworld.forge import GeneWorldForge
    w = dom.world
    key = w.class_keys(cl, "best")[0]
    g = Gene(id=f"gene_{cl}_best", signals_match=w.keywords[cl][:2],
             strategy=[f"Read the failing {cl} handler.", w.strategy_step(key), "Re-run the public check."],
             summary=f"Fix {cl} handler failures", validation=["python check.py"])
    before = {"mod.py": w.module(cl, "buggy"), "check.py": w.check_script(cl)}
    after = {**before, "mod.py": w.module(cl, "fixed")}
    return GeneWorldForge(w)._bundle(g, "alice", cl, before, after,
                                     claims={"confidence": 0.9, "files": 1, "lines": 2, "score": 0.9, "streak": 2})


def test_safe_consumer_never_retests_a_rejected_asset():
    from rsi.domains.geneworld import GeneWorldModel, make_domain
    dom = make_domain()
    hub = SafeHub(TaskBank(dom, dom.seed_artifact(), GeneWorldModel(0.0, "ref"), split="test", n=24, n_off=12, k=4),
                  seed=0)
    b = _honest_bundle(dom)
    assert hub.publish(b, "alice").status == "verified"
    ag = AgentNode("bob", dom, dom.seed_artifact(), llm_task=GeneWorldModel(0.0), config=Config(mode="safe", seed=0),
                   hub=hub)
    ag.quarantine.test = lambda g: type("Q", (), {"promote": False, "reason": "forced reject", "dS": 0.0,
                                                   "delta": 0.0, "n": 3, "proof": {"tasks_hash": "x"}})()
    tasks = [t for t in dom.tasks.split("evolve") if t.family == "c03"]
    ag.cycle(tasks[0])
    ag.cycle(tasks[1])
    assert ag.n_quarantined == 1 and ag.n_quarantine_rejected == 1


# ----------------------------------------------------------------------------- locked katas grader
def test_katas_grader_cannot_be_spoofed():
    from rsi.domains.katas.domain import check_asserts
    from rsi.domains.katas.katas import KATAS
    k = KATAS[0]
    n = len(k.hidden)
    assert check_asserts(k.correct, k.hidden)[0] == n
    assert check_asserts(k.buggy[0], k.hidden)[0] < n
    marker = f"import os\nprint('__RSI_OK__', {n})\nos._exit(0)\n"
    always = f"class _A:\n    __eq__ = lambda s, o: True\n    __bool__ = lambda s: True\ndef {k.fn}(*a, **kw):\n" \
             "    return _A()\n"
    assert check_asserts(marker, k.hidden)[0] == 0
    assert check_asserts(always, k.hidden)[0] == 0
