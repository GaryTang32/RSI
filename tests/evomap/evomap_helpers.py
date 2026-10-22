"""Shared builders for the evomap tests."""
from __future__ import annotations

import json
from pathlib import Path

from rsi.core import FunctionDomain, MockLLM, TaskSuite
from rsi.domains.geneworld import GeneWorldModel
from rsi.domains.geneworld.forge import GeneWorldForge
from rsi.domains.katas import KataMockProposer, KatasDomain, KataSimSolver, seed_harness
from rsi.evomap import Capsule, Config, EvolutionEvent, Gene, LocalStore, SafeHub, TaskBank, run
from rsi.evomap.hub import Bundle

HINT = " (it MUST fail before the fix and pass after it, e.g. the public check script)"


def fenced(gene: dict) -> str:
    return "```json\n" + json.dumps(gene) + "\n```"


def json_writer(gene: dict, name: str = "mock") -> MockLLM:
    return MockLLM(lambda p, s, seed, i: fenced(gene), name=name)


def kilo_model(name: str) -> MockLLM:
    """Frozen model: converts kilo-units only when its guidance says to multiply by 1000."""
    def respond(prompt, system, seed, i):
        return str(json.loads(prompt)["value"] * (1000 if "multiply by 1000" in (system or "").lower() else 1))

    return MockLLM(respond, name=name)


def units_domain(tasks: list, splits: dict) -> FunctionDomain:
    """Unit conversion with numeric outputs; the harness prompt is every artifact file."""
    def execute(artifact, task, seed, llm):
        guidance = "\n".join(artifact[p] for p in sorted(artifact))
        return float(llm.complete(json.dumps(task.input), system=guidance, seed=seed, role="task").text)

    return FunctionDomain(TaskSuite(tasks, splits), execute,
                          lambda task, out: (float(abs(out - task.target) < 1e-9), ""), name="units")


class ShallowWriter(MockLLM):
    """Always writes the same shallow dates card, recording every prompt."""

    def __init__(self, name: str = "shallow-writer"):
        self.prompts = []
        super().__init__(self._respond, name=name)

    def _respond(self, prompt, system, seed, i):
        self.prompts.append(prompt)
        return fenced({"id": f"gene_dates_shallow_{len(self.prompts)}", "category": "repair",
                       "signals_match": ["dates", "calendar"], "summary": "Be careful with dates.",
                       "strategy": ["Read the spec.", "Write code.", "Check examples."],
                       "avoid": ["Overthinking."], "validation": ["python smoke_test.py"]})


def honest_bundle(dom, key_kind: str = "best", cl: str = "c03", author: str = "alice",
                  validation=("python check.py",), extra: dict | None = None, gene_id: str | None = None) -> Bundle:
    """A GeneWorld gene with real, discriminative proof material."""
    w = dom.world
    g = Gene(id=gene_id or f"gene_{cl}_{key_kind}", signals_match=w.keywords[cl][:2],
             strategy=[f"Read the failing {cl} handler.", w.strategy_step(w.class_keys(cl, key_kind)[0]),
                       "Re-run the public check."],
             summary=f"Fix {cl} handler failures", validation=list(validation))
    before = {"mod.py": w.module(cl, "buggy"), "check.py": w.check_script(cl)}
    after = {**before, **(extra or {}), "mod.py": w.module(cl, "fixed")}
    return GeneWorldForge(w)._bundle(g, author, cl, before, after,
                                     claims={"confidence": 0.9, "files": 1, "lines": 2, "score": 0.9, "streak": 2})


def safe_hub(dom, **kw) -> SafeHub:
    bank = TaskBank(dom, dom.seed_artifact(), GeneWorldModel(0.0, "ref"), split="test", n=24, n_off=12, k=4)
    return SafeHub(bank, seed=0, **kw)


def distill_store(strategy: list, validation: list) -> LocalStore:
    """A store whose source gene has ten successful capsules."""
    st = LocalStore(node_id="d")
    st.upsert_gene(Gene(id="gene_src", signals_match=["x"], strategy=strategy, validation=validation))
    for i in range(10):
        st.upsert_capsule(Capsule(id=f"c{i}", gene="gene_src", trigger=["x", "y"], summary="fixed it", confidence=0.9,
                                  outcome={"status": "success", "score": 0.9}))
        st.append_event(EvolutionEvent(id=f"e{i}", mutation_id="m"))
    return st


def katas_population(out: Path, trace: bool = True, monitor: bool = True, publish_log: list | None = None):
    """agent0 evolves and publishes to a SafeHub, agent1 only consumes; returns (result0, result1, hub)."""
    dom, h = KatasDomain(scheme="audit"), seed_harness()
    hub = SafeHub(TaskBank(dom, h, KataSimSolver(0.0, name="hub-ref"), split="test", n=24, n_off=4, k=4), seed=0)
    if publish_log is not None:
        publish = hub.publish

        def logged(b, author):
            publish_log.append((author, b.gene.get("id"), (b.gene.get("provenance") or {}).get("kind")))
            return publish(b, author)

        hub.publish = logged
    kw = dict(mode="safe", quarantine_min_tasks=1, trace=trace, shadow_monitor=monitor, trace_baseline_k=2,
              monitor_k=2)
    a = run(dom, h, llm_task=KataSimSolver(0.0, name="sim-a"), llm_propose=KataMockProposer(0.7),
            config=Config(cycles=12, seed=0, validation_hint=HINT, **kw), hub=hub, name="agent0",
            out_dir=out / "agent0")
    b = run(dom, h, llm_task=KataSimSolver(0.0, name="sim-b"), llm_propose=None,
            config=Config(cycles=8, seed=1, **kw), hub=hub, name="agent1", out_dir=out / "agent1")
    return a, b, hub
