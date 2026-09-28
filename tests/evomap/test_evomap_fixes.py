"""Regressions for the claim-audit fixes (docs/methods/evomap/claims-audit.md "Fix log"; validation/evomap/AUDIT.md A1, A7, X10).

Every test here fails on the pre-fix code:

* N3  the composite's estimate-drift penalty reads Evolver's dead key (never fires) unless explicitly enabled;
* N4  composite rounding is JavaScript ``Math.round(x * 100) / 100`` (0.955 -> 0.96);
* N5  ``asset_id`` matches @evomap/gep-sdk@1.14.0 for astral-vs-high-BMP keys and integers >= 2**53 / 1e21;
* N6  the heuristic distiller's fallback validation is Evolver's ``node --test`` port, filtered to an EMPTY list;
* N8  faithful mode searches the hub BEFORE local selection and injects a hub hit NEXT TO the local gene;
* N9  hub metrics carry a study-style "trivial test command" share next to the discriminative vacuity share;
* N10 the naive hub also surfaces assets by semantic similarity (no literal pattern hit needed);
* A1  safe mode judges a new gene on a paired retry sample, not on one retry;
* A7  ``n_promoted`` counts status ``promoted`` only; ``n_admitted`` is the admitted tier;
* X10 ``poisoned_in_stores`` is split into hub poison vs self-written harmful cards.
"""
from __future__ import annotations

import json

from rsi.core import Artifact, FunctionDomain, MockLLM, Task, TaskSuite
from rsi.domains.geneworld import GeneWorldModel, make_domain
from rsi.domains.geneworld.forge import GeneWorldForge
from rsi.domains.katas import KataMockProposer, KatasDomain, KataSimSolver, seed_harness
from rsi.evomap import (AgentNode, Capsule, Config, EvolutionEvent, Gene, LocalStore, NaiveEvoMapHub, ReuseMetrics,
                        SafeHub, TaskBank)
from rsi.evomap.distill import Distiller
from rsi.evomap.hashing import asset_id, canonicalize
from rsi.evomap.hub import Bundle
from rsi.evomap.solidify import composite_score, js_round2
from rsi.evomap.validation import ValidationReport, ValidationResult

HINT = " (it MUST fail before the fix and pass after it, e.g. the public check script)"


# ----------------------------------------------------------------------------- N5
def test_asset_id_matches_gep_sdk_on_edge_cases():
    # reference ids computed with @evomap/gep-sdk@1.14.0 computeAssetId in node 22
    # (scratchpad/claims_evomap/n5_vec.mjs); JSON.parse turns the big integers into doubles first
    keys = {"！": 1, "\U0001F600": 2, "a": 3}
    assert canonicalize(keys) == '{"a":3,"\U0001F600":2,"！":1}'       # UTF-16 order: surrogates < U+FF01
    assert asset_id(keys) == "sha256:8b720842e3e0b138369301fb28d74fa4ee3ef131494632cec3e680d1447b430a"
    nums = {"n": 10 ** 21, "m": 2 ** 53 + 1, "k": 123456789012345678901234}
    assert canonicalize(nums) == '{"k":1.2345678901234569e+23,"m":9007199254740992,"n":1e+21}'
    assert asset_id(nums) == "sha256:3bde82888de0027129ad73fa298e35265761312b90517c6d0016a2333d7a04f4"
    assert canonicalize({"x": 2 ** 53 - 1, "y": -(10 ** 20)}) == '{"x":9007199254740991,"y":-100000000000000000000}'


# ----------------------------------------------------------------------------- N3 + N4
def _val(component_ok: bool = True) -> ValidationResult:
    return ValidationResult(component_ok, ValidationReport(id="vr", gene_id="g", commands=[{"ok": True}],
                                                           overall_ok=True), 1, 1, 0, [])


def test_composite_rounds_like_javascript():
    assert js_round2(0.955) == 0.96 and round(0.955, 2) == 0.95        # Python's round disagrees here
    assert js_round2(0.9450000000000001) == 0.95 and js_round2(0.94) == 0.94
    g = Gene(id="gene_x", signals_match=["a"], strategy=["s"])
    from rsi.evomap.mutation import Mutation
    mut = Mutation(id="m", category="repair", trigger_signals=[], target="t", expected_effect="e",
                   risk_level="medium", rationale="")
    s = composite_score(n_signals=4, gene=g, mutation=mut, blast={"files": 1, "lines": 3}, max_files=12,
                        estimate=None, n_violations=0, validation=_val(), n_protocol=0)
    assert s == 0.96          # Evolver: Math.round(95.5) / 100; the old Python round gave 0.95


def test_estimate_drift_penalty_is_dead_as_in_evolver_unless_enabled():
    g = Gene(id="gene_x", signals_match=["a"], strategy=["s"])
    kw = dict(n_signals=4, gene=g, mutation=None, blast={"files": 5, "lines": 10}, max_files=12,
              estimate={"files": 1, "lines": 80}, n_violations=0, validation=_val(), n_protocol=0)
    faithful = composite_score(**kw)                                      # reads estimate["files_changed"]: absent
    penalised = composite_score(**kw, estimate_key="files")               # ratio 5 > 3 -> blast x 0.5
    # sig .05*.8 + sel .1*.9 + mut .05*.3 + blast .15*1.0 + .25 + .25 + .1 + .05 = 0.945 -> 0.95 (JS);
    # with the penalty the blast component is 0.5 -> 0.87 (0.8700000000000001 before rounding)
    assert faithful == 0.95 and penalised == 0.87
    assert faithful == composite_score(**{**kw, "estimate": None})


# ----------------------------------------------------------------------------- N6
def test_heuristic_distiller_fallback_validation_is_empty_like_evolver():
    st = LocalStore(node_id="d")
    st.upsert_gene(Gene(id="gene_src", signals_match=["x"], strategy=["a", "b", "c", "d"], validation=[]))
    for i in range(10):
        st.upsert_capsule(Capsule(id=f"c{i}", gene="gene_src", trigger=["x", "y"], summary="fixed it", confidence=0.9,
                                  outcome={"status": "success", "score": 0.9}))
        st.append_event(EvolutionEvent(id=f"e{i}", mutation_id="m"))
    r = Distiller(mode="faithful").auto_distill(st)
    # Evolver: ["node --test"] -> the allowlist drops it -> []  (the old port shipped ["python --version"])
    assert r.ok and r.gene.validation == []
    assert Distiller(mode="faithful").fallback_validation == ["python --test"]


# ----------------------------------------------------------------------------- N8
def _units():
    tasks = [Task(f"u{i}", {"value": i + 1}, float((i + 1) * 1000), "length") for i in range(12)]
    suite = TaskSuite(tasks, {"evolve": [t.id for t in tasks[:6]], "holdout": [t.id for t in tasks[6:]]})

    def execute(artifact, task, seed, llm):
        return float(llm.complete(json.dumps(task.input), system="\n".join(artifact[p] for p in sorted(artifact)),
                                  seed=seed, role="task").text)

    return FunctionDomain(suite, execute, lambda t, o: (float(abs(o - t.target) < 1e-9), ""), name="units")


def _model():
    return MockLLM(lambda p, s, seed, i: str(json.loads(p)["value"] * (1000 if "multiply by 1000" in (s or "").lower()
                                                                        else 1)), name="m")


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


def test_faithful_reference_mode_injects_the_hub_hit_next_to_the_local_gene():
    hub = _hub_with_kilo_gene()
    local = Gene(id="gene_local_units", signals_match=["task:length"], summary="Be careful with units.",
                 strategy=["Read the task.", "Answer with a number.", "Double-check."])
    st = LocalStore(node_id="bob")
    st.upsert_gene(local)
    ag = AgentNode("bob", _units(), Artifact({"agent.md": "Convert.\n"}), llm_task=_model(), store=st, hub=hub,
                   config=Config(mode="faithful", trace=False))
    cr = ag.cycle(ag.decision_tasks[0])
    # Evolver: last_run.selected_gene_id stays the LOCAL gene; the hub hit is a STRONG REFERENCE next to it
    assert cr.gene_id == "gene_local_units" and cr.hub_gene_id == "gene_kilo" and cr.source == "hub"
    assert [g.id for g in ag.last_genes] == ["gene_local_units", "gene_kilo"]
    assert cr.task_success                          # the reference reached the solver
    assert "gene_kilo" not in ag.store.genes        # a reference is never stored by solidify
    assert ag.store.events[-1].source_type == "reference"


def test_faithful_mode_searches_the_hub_before_local_selection():
    hub = NaiveEvoMapHub()                          # empty hub -> a miss
    calls = []
    orig = hub.search

    def search(signals, k=5, consumer=None):
        calls.append(list(signals))
        return orig(signals, k=k, consumer=consumer)

    hub.search = search
    st = LocalStore(node_id="bob")
    st.upsert_gene(Gene(id="gene_local_units", signals_match=["task:length"], strategy=["a", "b", "c"]))

    class ErrExtractor:
        def extract(self, ctx):
            return ["task:length", "log_error"]

    ag = AgentNode("bob", _units(), Artifact({"agent.md": "Convert.\n"}), llm_task=_model(), store=st, hub=hub,
                   extractor=ErrExtractor(), config=Config(mode="faithful", trace=False))
    cr = ag.cycle(ag.decision_tasks[0])
    assert calls, "faithful mode must ask the hub even when a local gene fits"
    # enrich.js: a miss with problem signals adds hub_search_miss_with_problem BEFORE selection
    assert "hub_search_miss_with_problem" in cr.signals and cr.gene_id == "gene_local_units"



def test_legacy_replace_mode_is_the_old_naive_reference_path_not_quarantine():
    # verifier finding: reuse_mode="replace" fell through to the quarantine branch of _consult_hub, so the
    # documented "old behaviour" option ran a local A/B instead of the pre-fix naive replace
    hub = _hub_with_kilo_gene()
    hub.search_mode = "legacy"
    st = LocalStore(node_id="bob")
    st.upsert_gene(Gene(id="gene_local_units", signals_match=["task:length"], summary="Be careful with units.",
                        strategy=["Read the task.", "Answer with a number.", "Double-check."]))
    ag = AgentNode("bob", _units(), Artifact({"agent.md": "Convert.\n"}), llm_task=_model(), store=st, hub=hub,
                   config=Config(mode="faithful", reuse_mode="replace", hub_when="always", trace=False))
    cr = ag.cycle(ag.decision_tasks[0])
    # pre-fix faithful "reference" (git 79e464d) on the same setup: gene_kilo replaces the local gene, is stored,
    # the event says "reference", and no quarantine ran
    assert cr.gene_id == "gene_kilo" and cr.source == "hub" and cr.quarantine is None and ag.n_quarantined == 0
    assert "gene_kilo" in ag.store.genes and ag.store.events[-1].source_type == "reference"


def test_config_estimate_drift_penalty_reaches_the_solidifier():
    # verifier finding: Config.estimate_drift_penalty was never passed to the Solidifier (the opt-in was dead)
    mk = lambda **kw: AgentNode("a", _units(), Artifact({"agent.md": "x"}), llm_task=_model(),  # noqa: E731
                                config=Config(mode="faithful", trace=False, **kw))
    assert mk().solidifier.estimate_drift_penalty is False
    assert mk(estimate_drift_penalty=True).solidifier.estimate_drift_penalty is True

# ----------------------------------------------------------------------------- N10
def test_naive_hub_surfaces_assets_by_semantic_similarity():
    hub = _hub_with_kilo_gene()
    views = hub.search(["task:mass", "kilo", "units"])       # no literal pattern hit on 'task:length'
    assert [v.gene["id"] for v in views] == ["gene_kilo"] and views[0].similarity > 0
    assert hub.semantic_query(["task:mass", "errsig:x", "kilo"]) == "mass kilo"
    assert NaiveEvoMapHub(search_mode="signal").search(["kilo"]) == []
    sig = hub.search(["task:length"])                         # signal hit, also returned by the semantic search
    assert sig and sig[0].similarity > 0
    hub_sig = _hub_with_kilo_gene()
    hub_sig.search_mode = "signal"
    assert hub_sig.search(["task:length"])[0].similarity == 0.0   # HUBSEARCH_SEMANTIC=false: no similarity term


# ----------------------------------------------------------------------------- N9 + A7
def _bundle(dom, validation, extra, cl="c03", author="alice"):
    w = dom.world
    key = w.class_keys(cl, "best")[0]
    g = Gene(id=f"gene_{cl}_{abs(hash(tuple(validation))) % 10 ** 6}", signals_match=w.keywords[cl][:2],
             strategy=[f"Read the failing {cl} handler.", w.strategy_step(key), "Re-run the public check."],
             summary=f"Fix {cl} handler failures", validation=list(validation))
    before = {"mod.py": w.module(cl, "buggy"), "check.py": w.check_script(cl)}
    after = {**before, **extra, "mod.py": w.module(cl, "fixed")}
    return GeneWorldForge(w)._bundle(g, author, cl, before, after,
                                     claims={"confidence": 0.9, "files": 1, "lines": 2, "score": 0.9, "streak": 2})


def test_trivial_command_share_is_reported_next_to_discriminative_vacuity():
    dom = make_domain()
    w = dom.world
    hub = NaiveEvoMapHub()
    for kind in ("version", "print_only", "weak_assert"):
        val, extra = w.vacuous_validation(kind, "c03")
        assert hub.publish(_bundle(dom, val, extra), "alice").status == "promoted"
    assert hub.publish(_bundle(dom, ["python check.py"], {}), "alice").status == "promoted"
    m = ReuseMetrics().compute(hub)
    # discriminative notion (ours): version, print_only, weak_assert are vacuous -> 3/4
    assert m["vacuous_share_promoted"] == 0.75
    # study notion (console.log class): only version and print_only are trivial as written -> 2/4
    assert m["trivial_command_share_promoted"] == 0.5


def test_n_promoted_counts_promoted_status_only():
    dom = make_domain()
    hub = SafeHub(TaskBank(dom, dom.seed_artifact(), GeneWorldModel(0.0, "ref"), split="test", n=24, n_off=12, k=4),
                  seed=0)
    hub.register("alice", cluster="alice")
    d = hub.publish(_bundle(dom, ["python check.py"], {}), "alice")
    assert d.status == "verified"
    m = hub.metrics()
    assert m["n_promoted"] == 0 and m["n_admitted"] == 1 and m["admitted_states"] == ["promoted", "verified"]


# ----------------------------------------------------------------------------- A1
class _ShallowWriter(MockLLM):
    def __init__(self):
        self.n = 0
        super().__init__(self._respond, name="shallow-writer")

    def _respond(self, prompt, system, seed, i):
        self.n += 1
        return "```json\n" + json.dumps({"id": f"gene_dates_shallow_{self.n}", "category": "repair",
                                         "signals_match": ["dates", "calendar"], "summary": "Be careful with dates.",
                                         "strategy": ["Read the spec.", "Write code.", "Check examples."],
                                         "avoid": ["Overthinking."], "validation": ["python smoke_test.py"]}) + "\n```"


def _dates_agent(writer, **kw):
    dom = KatasDomain(scheme="audit")
    ag = AgentNode("a", dom, seed_harness(), llm_task=KataSimSolver(0.0, name="s"), llm_propose=writer,
                   config=Config(mode="safe", seed=0, validation_hint=HINT, trace=False, **kw))
    return ag, next(t for t in dom.tasks.split("evolve") if t.family == "dates")


def test_a_useless_new_gene_is_not_kept_on_a_lucky_retry():
    ag, task = _dates_agent(_ShallowWriter())
    for _ in range(8):
        ag.cycle(task)
    # pre-fix (one retry): gene_dates_shallow_3 was kept after a single lucky retry
    assert not [g for g in ag.store.genes if g.startswith("gene_dates_shallow")]
    smp = [r.new_gene_retries for r in ag.results if r.new_gene_retries]
    assert smp and all(len(s["with"]) == 3 and len(s["without"]) == 3 and s["check"] == "paired" for s in smp)
    # the old single-sample rule is still available explicitly and still admits the lucky card
    ag1, task1 = _dates_agent(_ShallowWriter(), new_gene_retries=1, new_gene_check="single")
    for _ in range(8):
        ag1.cycle(task1)
    assert [g for g in ag1.store.genes if g.startswith("gene_dates_shallow")]


def test_a_real_new_gene_is_still_kept():
    ag, task = _dates_agent(KataMockProposer(1.0))
    for _ in range(6):
        ag.cycle(task)
    assert [g for g in ag.store.genes if g.startswith("gene_")], "a real class lesson must still pass the sample"


# ----------------------------------------------------------------------------- X10
def test_poisoned_in_stores_is_split_by_origin():
    from rsi.evomap import AgentSpec, PopulationSimulator
    dom = make_domain()
    w = dom.world
    hub = NaiveEvoMapHub()
    specs = [AgentSpec("a0_honest", "honest"), AgentSpec("a1_poisoner", "poisoner", farm_rate=2)]
    sim = PopulationSimulator(dom, dom.seed_artifact(), hub, specs, config=Config(mode="faithful", reuse_mode="replace"),
                              model_factory=lambda sp: GeneWorldModel(0.0, sp.name), forge=GeneWorldForge(w), seed=0)
    ag = sim.agents["a0_honest"]
    bad = w.class_keys("c01", "harmful")[0]
    own = Gene(id="gene_own_bad", signals_match=["x"], strategy=[w.strategy_step(bad), "b", "c"],
               provenance={"kind": "evolved", "author": "a0_honest"})
    ag.store.upsert_gene(own)
    b = GeneWorldForge(w).poison_bundle("a1_poisoner", __import__("random").Random(0), 0)
    rec = hub.publish(b, "a1_poisoner")
    took = Gene.from_dict(b.gene)
    took.parent = rec.asset_id                           # how a direct / replace consumer stores a hub gene
    ag.store.upsert_gene(took)
    # a re-fetched hub gene overwrites the stored copy without the parent link: its foreign author still counts
    refetched = Gene.from_dict(GeneWorldForge(w).poison_bundle("a1_poisoner", __import__("random").Random(1), 1).gene)
    ag.store.upsert_gene(refetched)
    split = sim.poisoned_in_stores_split()
    assert split == {"hub": 2, "self": 1} and sim.poisoned_in_stores() == 3
    s = sim.summary()
    assert s["poisoned_in_stores_hub"] == 2 and s["poisoned_in_stores_self"] == 1
