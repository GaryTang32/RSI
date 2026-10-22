"""Claim-audit fixes: hashing edge cases, JS rounding, hub-first reference reuse, metrics and the A1 retry sample."""
from __future__ import annotations

import random
from types import SimpleNamespace

from evomap_helpers import HINT, ShallowWriter, distill_store, honest_bundle, kilo_model, safe_hub, units_domain
from rsi.core import Artifact, Task
from rsi.domains.geneworld import GeneWorldModel
from rsi.domains.geneworld.forge import GeneWorldForge
from rsi.domains.katas import KataMockProposer, KatasDomain, KataSimSolver, seed_harness
from rsi.evomap import (AgentNode, AgentSpec, Capsule, Config, Gene, LocalStore, NaiveEvoMapHub, PopulationSimulator,
                        ReuseMetrics)
from rsi.evomap.distill import Distiller
from rsi.evomap.hashing import asset_id, canonicalize
from rsi.evomap.hub import Bundle
from rsi.evomap.mutation import Mutation
from rsi.evomap.solidify import composite_score, js_round2
from rsi.evomap.validation import ValidationReport, ValidationResult


def test_asset_id_matches_gep_sdk_on_edge_cases():
    # ids from @evomap/gep-sdk@1.14.0 computeAssetId in node 22; JSON.parse turns big integers into doubles first
    keys = {"！": 1, "\U0001F600": 2, "a": 3}
    assert canonicalize(keys) == '{"a":3,"\U0001F600":2,"！":1}'  # UTF-16 order: surrogates < U+FF01
    assert asset_id(keys) == "sha256:8b720842e3e0b138369301fb28d74fa4ee3ef131494632cec3e680d1447b430a"
    nums = {"n": 10 ** 21, "m": 2 ** 53 + 1, "k": 123456789012345678901234}
    assert canonicalize(nums) == '{"k":1.2345678901234569e+23,"m":9007199254740992,"n":1e+21}'
    assert asset_id(nums) == "sha256:3bde82888de0027129ad73fa298e35265761312b90517c6d0016a2333d7a04f4"
    assert canonicalize({"x": 2 ** 53 - 1, "y": -(10 ** 20)}) == '{"x":9007199254740991,"y":-100000000000000000000}'


def _val() -> ValidationResult:
    return ValidationResult(True, ValidationReport(id="vr", gene_id="g", commands=[{"ok": True}], overall_ok=True),
                            1, 1, 0, [])


def test_composite_rounds_like_javascript():
    assert js_round2(0.955) == 0.96 and round(0.955, 2) == 0.95
    assert js_round2(0.9450000000000001) == 0.95 and js_round2(0.94) == 0.94
    mut = Mutation(id="m", category="repair", trigger_signals=[], target="t", expected_effect="e",
                   risk_level="medium", rationale="")
    s = composite_score(n_signals=4, gene=Gene(id="gene_x", signals_match=["a"], strategy=["s"]), mutation=mut,
                        blast={"files": 1, "lines": 3}, max_files=12, estimate=None, n_violations=0,
                        validation=_val(), n_protocol=0)
    assert s == 0.96  # Math.round(95.5) / 100


def test_estimate_drift_penalty_is_dead_as_in_evolver_unless_enabled():
    kw = dict(n_signals=4, gene=Gene(id="gene_x", signals_match=["a"], strategy=["s"]), mutation=None,
              blast={"files": 5, "lines": 10}, max_files=12, estimate={"files": 1, "lines": 80}, n_violations=0,
              validation=_val(), n_protocol=0)
    faithful = composite_score(**kw)  # reads estimate["files_changed"], which is absent
    penalised = composite_score(**kw, estimate_key="files")  # ratio 5 > 3 halves the blast component
    # .05*.8 + .1*.9 + .05*.3 + .15*1.0 + .25 + .25 + .1 + .05 = 0.945 -> 0.95 (JS); blast .5 -> 0.87
    assert faithful == 0.95 and penalised == 0.87
    assert faithful == composite_score(**{**kw, "estimate": None})


def test_heuristic_distiller_fallback_validation_is_empty_like_evolver():
    r = Distiller(mode="faithful").auto_distill(distill_store(["a", "b", "c", "d"], []))
    assert r.ok and r.gene.validation == []  # Evolver's ["node --test"] is dropped by the allowlist
    assert Distiller(mode="faithful").fallback_validation == ["python --test"]


def _units():
    tasks = [Task(f"u{i}", {"value": i + 1}, float((i + 1) * 1000), "length") for i in range(12)]
    return units_domain(tasks, {"evolve": [t.id for t in tasks[:6]], "holdout": [t.id for t in tasks[6:]]})


def _hub_with_kilo_gene():
    hub = NaiveEvoMapHub()
    g = Gene(id="gene_kilo", signals_match=["task:length"], summary="Convert kilo-units to base units.",
             strategy=["Read the value.", "Multiply by 1000 for kilo prefixes.", "Return a number."],
             validation=["python --version"]).stamp()
    cap = Capsule(id="capsule_kilo", gene=g.id, trigger=["task:length"], summary="converted km to m with the gene",
                  confidence=0.9, blast_radius={"files": 1, "lines": 2}, outcome={"status": "success", "score": 0.9},
                  success_streak=3, content="\n".join(g.strategy) * 3).stamp()
    d = hub.publish(Bundle(gene=g.to_dict(), capsule=cap.to_dict(), report={"overall_ok": True}), "alice")
    assert d.status == "promoted"
    return hub


def _local_store(**gene_kw):
    st = LocalStore(node_id="bob")
    st.upsert_gene(Gene(id="gene_local_units", signals_match=["task:length"], **gene_kw))
    return st


def _careful_store():
    return _local_store(summary="Be careful with units.",
                        strategy=["Read the task.", "Answer with a number.", "Double-check."])


def _agent(hub, store, extractor=None, **cfg):
    return AgentNode("bob", _units(), Artifact({"agent.md": "Convert.\n"}), llm_task=kilo_model("m"), store=store,
                     hub=hub, extractor=extractor, config=Config(mode="faithful", trace=False, **cfg))


def test_faithful_reference_mode_injects_the_hub_hit_next_to_the_local_gene():
    ag = _agent(_hub_with_kilo_gene(), _careful_store())
    cr = ag.cycle(ag.decision_tasks[0])
    # Evolver keeps the local gene selected; the hub hit is a strong reference next to it
    assert cr.gene_id == "gene_local_units" and cr.hub_gene_id == "gene_kilo" and cr.source == "hub"
    assert [g.id for g in ag.last_genes] == ["gene_local_units", "gene_kilo"]
    assert cr.task_success
    assert "gene_kilo" not in ag.store.genes
    assert ag.store.events[-1].source_type == "reference"


def test_faithful_mode_searches_the_hub_before_local_selection():
    hub = NaiveEvoMapHub()
    calls = []
    orig = hub.search

    def search(signals, k=5, consumer=None):
        calls.append(list(signals))
        return orig(signals, k=k, consumer=consumer)

    hub.search = search
    extractor = SimpleNamespace(extract=lambda ctx: ["task:length", "log_error"])
    ag = _agent(hub, _local_store(strategy=["a", "b", "c"]), extractor=extractor)
    cr = ag.cycle(ag.decision_tasks[0])
    assert calls, "faithful mode must ask the hub even when a local gene fits"
    # enrich.js: a miss with problem signals adds hub_search_miss_with_problem before selection
    assert "hub_search_miss_with_problem" in cr.signals and cr.gene_id == "gene_local_units"


def test_legacy_replace_mode_is_the_old_naive_reference_path_not_quarantine():
    hub = _hub_with_kilo_gene()
    hub.search_mode = "legacy"
    ag = _agent(hub, _careful_store(), reuse_mode="replace", hub_when="always")
    cr = ag.cycle(ag.decision_tasks[0])
    assert cr.gene_id == "gene_kilo" and cr.source == "hub" and cr.quarantine is None and ag.n_quarantined == 0
    assert "gene_kilo" in ag.store.genes and ag.store.events[-1].source_type == "reference"


def test_config_estimate_drift_penalty_reaches_the_solidifier():
    def make(**kw):
        return AgentNode("a", _units(), Artifact({"agent.md": "x"}), llm_task=kilo_model("m"),
                         config=Config(mode="faithful", trace=False, **kw))

    assert make().solidifier.estimate_drift_penalty is False
    assert make(estimate_drift_penalty=True).solidifier.estimate_drift_penalty is True


def test_naive_hub_surfaces_assets_by_semantic_similarity():
    hub = _hub_with_kilo_gene()
    views = hub.search(["task:mass", "kilo", "units"])  # no literal pattern hit on 'task:length'
    assert [v.gene["id"] for v in views] == ["gene_kilo"] and views[0].similarity > 0
    assert hub.semantic_query(["task:mass", "errsig:x", "kilo"]) == "mass kilo"
    assert NaiveEvoMapHub(search_mode="signal").search(["kilo"]) == []
    sig = hub.search(["task:length"])
    assert sig and sig[0].similarity > 0
    hub_sig = _hub_with_kilo_gene()
    hub_sig.search_mode = "signal"
    assert hub_sig.search(["task:length"])[0].similarity == 0.0


def _bundle(dom, validation, extra):
    return honest_bundle(dom, validation=validation, extra=extra,
                         gene_id=f"gene_c03_{abs(hash(tuple(validation))) % 10 ** 6}")


def test_trivial_command_share_is_reported_next_to_discriminative_vacuity(gw):
    hub = NaiveEvoMapHub()
    for kind in ("version", "print_only", "weak_assert"):
        val, extra = gw.world.vacuous_validation(kind, "c03")
        assert hub.publish(_bundle(gw, val, extra), "alice").status == "promoted"
    assert hub.publish(_bundle(gw, ["python check.py"], {}), "alice").status == "promoted"
    m = ReuseMetrics().compute(hub)
    assert m["vacuous_share_promoted"] == 0.75  # discriminative: version, print_only and weak_assert
    assert m["trivial_command_share_promoted"] == 0.5  # trivial as written: version and print_only


def test_n_promoted_counts_promoted_status_only(gw):
    hub = safe_hub(gw)
    hub.register("alice", cluster="alice")
    assert hub.publish(_bundle(gw, ["python check.py"], {}), "alice").status == "verified"
    m = hub.metrics()
    assert m["n_promoted"] == 0 and m["n_admitted"] == 1 and m["admitted_states"] == ["promoted", "verified"]


def _dates_agent(writer, **kw):
    dom = KatasDomain(scheme="audit")
    ag = AgentNode("a", dom, seed_harness(), llm_task=KataSimSolver(0.0, name="s"), llm_propose=writer,
                   config=Config(mode="safe", seed=0, validation_hint=HINT, trace=False, **kw))
    return ag, next(t for t in dom.tasks.split("evolve") if t.family == "dates")


def _shallow_genes(ag, task, cycles=8):
    for _ in range(cycles):
        ag.cycle(task)
    return [g for g in ag.store.genes if g.startswith("gene_dates_shallow")]


def test_a_useless_new_gene_is_not_kept_on_a_lucky_retry():
    ag, task = _dates_agent(ShallowWriter())
    assert not _shallow_genes(ag, task)
    smp = [r.new_gene_retries for r in ag.results if r.new_gene_retries]
    assert smp and all(len(s["with"]) == 3 and len(s["without"]) == 3 and s["check"] == "paired" for s in smp)
    # the old single-sample rule, still available, admits the lucky card
    assert _shallow_genes(*_dates_agent(ShallowWriter(), new_gene_retries=1, new_gene_check="single"))


def test_a_real_new_gene_is_still_kept():
    ag, task = _dates_agent(KataMockProposer(1.0))
    for _ in range(6):
        ag.cycle(task)
    assert [g for g in ag.store.genes if g.startswith("gene_")], "a real class lesson must still pass the sample"


def test_poisoned_in_stores_is_split_by_origin(gw):
    w = gw.world
    hub = NaiveEvoMapHub()
    specs = [AgentSpec("a0_honest", "honest"), AgentSpec("a1_poisoner", "poisoner", farm_rate=2)]
    sim = PopulationSimulator(gw, gw.seed_artifact(), hub, specs,
                              config=Config(mode="faithful", reuse_mode="replace"),
                              model_factory=lambda sp: GeneWorldModel(0.0, sp.name), forge=GeneWorldForge(w), seed=0)
    ag = sim.agents["a0_honest"]
    bad = w.class_keys("c01", "harmful")[0]
    ag.store.upsert_gene(Gene(id="gene_own_bad", signals_match=["x"], strategy=[w.strategy_step(bad), "b", "c"],
                              provenance={"kind": "evolved", "author": "a0_honest"}))
    b = GeneWorldForge(w).poison_bundle("a1_poisoner", random.Random(0), 0)
    rec = hub.publish(b, "a1_poisoner")
    took = Gene.from_dict(b.gene)
    took.parent = rec.asset_id  # how a direct / replace consumer stores a hub gene
    ag.store.upsert_gene(took)
    # a re-fetched hub gene overwrites the stored copy without the parent link; its foreign author still counts
    ag.store.upsert_gene(Gene.from_dict(GeneWorldForge(w).poison_bundle("a1_poisoner", random.Random(1), 1).gene))
    assert sim.poisoned_in_stores_split() == {"hub": 2, "self": 1} and sim.poisoned_in_stores() == 3
    s = sim.summary()
    assert s["poisoned_in_stores_hub"] == 2 and s["poisoned_in_stores_self"] == 1
