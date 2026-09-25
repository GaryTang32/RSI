"""Population simulation: many agents, one hub (spec §9.1, §9.3 Tier 1).

Agent kinds (:class:`AgentSpec.kind`):

``honest``    full :class:`AgentNode` cycles; publishes what its solidify keep rule accepted;
``inflator``  honest content, inflated claims (blast radius 1 file / 20 lines, confidence 0.99,
              streak 5, outcome score 0.99, report ok) - re-stamped so hashes still verify;
``freerider`` cycles but never publishes;
``farmer``    mass-publishes ``farm_rate`` variants per epoch with vacuous validation and
              inflated metadata (content from a domain :class:`Forge`);
``poisoner``  publishes harmful strategies (harmful step or injection string) with fabricated
              but *discriminative-looking* proof material and inflated metadata.

Abilities are heterogeneous (``ability`` -> the domain model, ``insight`` -> the
proposer), so X6 can ask whether weak models inherit strong models' genes.
"""
from __future__ import annotations

import copy
import random
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Callable, Optional, Protocol, Sequence

import numpy as np

from rsi.core import Artifact, Domain, LLM

from .agent import AgentNode, Behavior
from .assets import Clock, Gene
from .config import Config
from .hashing import asset_id
from .hub import Bundle
from .inject import Injector
from .metrics import ReuseMetrics
from .store import LocalStore


@dataclass
class AgentSpec:
    name: str
    kind: str = "honest"
    ability: float = 0.0
    insight: float = 0.5
    cluster: Optional[str] = None
    farm_rate: int = 5
    classes: Optional[list] = None          # task families this agent meets (None = all)
    tasks_per_epoch: int = 1
    config: Optional[Config] = None         # per-agent override


class InflatorBehavior(Behavior):
    kind = "inflator"

    def modify_bundle(self, bundle: Bundle, agent) -> Bundle:
        return inflate(bundle)


class FreeriderBehavior(Behavior):
    kind = "freerider"
    publishes = False


def inflate(bundle: Bundle, *, files: int = 1, lines: int = 20, confidence: float = 0.99, streak: int = 5,
            score: float = 0.99, fields: Sequence[str] = ("blast", "confidence", "streak", "score", "report")) -> Bundle:
    """Rewrite the claimed metadata of a bundle (and re-stamp asset ids so they verify)."""
    b = copy.deepcopy(bundle)
    c = b.capsule or {}
    if "blast" in fields:
        c["blast_radius"] = {"files": files, "lines": lines}
    if "confidence" in fields:
        c["confidence"] = confidence
    if "streak" in fields:
        c["success_streak"] = streak
    if "score" in fields:
        c["outcome"] = {"status": "success", "score": score}
    if c:
        c.pop("asset_id", None)
        c["asset_id"] = asset_id(c)
        b.capsule = c
    if "report" in fields:
        b.report = {**(b.report or {}), "overall_ok": True}
    return b


class Forge(Protocol):
    """Domain-specific adversary kit: builds farmer and poisoner bundles."""

    def farm_bundle(self, author: str, rng: random.Random, epoch: int) -> Bundle: ...
    def poison_bundle(self, author: str, rng: random.Random, epoch: int) -> Bundle: ...
    def is_poisoned(self, gene: Gene) -> bool: ...


class PopulationSimulator:
    def __init__(self, domain: Domain, harness: Artifact, hub, specs: Sequence[AgentSpec], *,
                 config: Config, model_factory: Callable[[AgentSpec], LLM],
                 proposer_factory: Optional[Callable[[AgentSpec], LLM]] = None, forge: Optional[Forge] = None,
                 truth: Optional[Callable[[Gene], float]] = None, injector: Optional[Injector] = None,
                 seed: int = 0, clock_step_s: float = 3600.0) -> None:
        self.domain, self.harness, self.hub = domain, harness, hub
        self.specs = list(specs)
        self.config = config
        self.forge = forge
        self.truth = truth
        self.rng = random.Random(f"pop-{seed}")
        self.seed = seed
        self.tasks = domain.tasks.split(config.split)
        self.agents: dict[str, AgentNode] = {}
        self.models: dict[str, LLM] = {}
        self.proposers: dict[str, Optional[LLM]] = {}
        self.clock = Clock(step_s=clock_step_s)
        for sp in self.specs:
            if sp.kind in ("farmer", "poisoner"):
                if hub is not None:
                    (hub.register(sp.name, cluster=sp.cluster) if hasattr(hub, "register")
                     else hub.credits.register(sp.name))
                continue
            model = model_factory(sp)
            prop = proposer_factory(sp) if proposer_factory else None
            beh = {"inflator": InflatorBehavior(), "freerider": FreeriderBehavior()}.get(sp.kind, Behavior())
            cfg = copy.deepcopy(sp.config or config)
            cfg.seed = seed
            self.models[sp.name], self.proposers[sp.name] = model, prop
            self.agents[sp.name] = AgentNode(sp.name, domain, harness, llm_task=model, llm_propose=prop, config=cfg,
                                             store=LocalStore(node_id=sp.name, clock=self.clock), hub=hub,
                                             injector=injector, behavior=beh, cluster=sp.cluster)
        self.cycles: list[dict] = []
        self.epochs: list[dict] = []

    def _task(self, sp: AgentSpec, rng: random.Random):
        pool = [t for t in self.tasks if sp.classes is None or t.family in sp.classes]
        return rng.choice(pool)

    def run(self, epochs: int, *, metrics_every: int = 0) -> dict:
        for ep in range(epochs):
            order = list(self.specs)
            self.rng.shuffle(order)
            for sp in order:
                arng = random.Random(f"{self.seed}-{sp.name}-{ep}")
                if sp.kind in ("farmer", "poisoner"):
                    if self.hub is None or self.forge is None:
                        continue
                    for _ in range(sp.farm_rate):
                        b = (self.forge.farm_bundle if sp.kind == "farmer" else self.forge.poison_bundle)(
                            sp.name, arng, ep)
                        d = self.hub.publish(b, sp.name)
                        self.cycles.append({"epoch": ep, "agent": sp.name, "kind": sp.kind, "published": d.status})
                    continue
                ag = self.agents[sp.name]
                for _ in range(sp.tasks_per_epoch):
                    task = self._task(sp, arng)
                    cr = ag.cycle(task)
                    row = {"epoch": ep, "agent": sp.name, "kind": sp.kind, "ability": sp.ability, **cr.to_json()}
                    if self.truth is not None and cr.gene_id and cr.gene_id in ag.store.genes:
                        row["gene_true_uplift"] = self.truth(ag.store.genes[cr.gene_id])
                    if self.forge is not None and cr.gene_id and cr.gene_id in ag.store.genes:
                        row["gene_poisoned"] = self.forge.is_poisoned(ag.store.genes[cr.gene_id])
                    self.cycles.append(row)
            if self.hub is not None:
                self.hub.advance_epoch()
            self.clock.tick(24)
            self.epochs.append(self._epoch_row(ep))
            if metrics_every and (ep + 1) % metrics_every == 0 and self.hub is not None:
                self.epochs[-1]["hub"] = self.hub_metrics()
        for ag in self.agents.values():
            ag.flush()
        return self.summary()

    def _epoch_row(self, ep: int) -> dict:
        rows = [r for r in self.cycles if r["epoch"] == ep and "task_success" in r]
        by = defaultdict(list)
        for r in rows:
            by[r["kind"]].append(r["task_success"])
        return {"epoch": ep, "solve_rate": float(np.mean([r["task_success"] for r in rows])) if rows else float("nan"),
                "by_kind": {k: float(np.mean(v)) for k, v in by.items()},
                "n_published": len(self.hub.order) if self.hub is not None else 0}

    def hub_metrics(self) -> dict:
        names = [sp.name for sp in self.specs]
        return ReuseMetrics(truth=self.truth, agents=names).compute(self.hub)

    def credits_by_kind(self) -> dict:
        if self.hub is None:
            return {}
        earned = self.hub.credits.earned_all([sp.name for sp in self.specs])
        tot = sum(max(0.0, v) for v in earned.values()) or 1.0
        out: dict = defaultdict(float)
        for sp in self.specs:
            out[sp.kind] += max(0.0, earned[sp.name]) / tot
        return dict(out)

    def poisoned_in_stores(self) -> int:
        if self.forge is None:
            return 0
        return sum(1 for ag in self.agents.values() for g in ag.store.genes.values() if self.forge.is_poisoned(g))

    def summary(self) -> dict:
        rows = [r for r in self.cycles if "task_success" in r]
        kinds = Counter(sp.kind for sp in self.specs)
        out = {"n_agents": len(self.specs), "kinds": dict(kinds), "n_cycles": len(rows),
               "solve_rate": float(np.mean([r["task_success"] for r in rows])) if rows else float("nan"),
               "solve_rate_by_kind": {k: float(np.mean([r["task_success"] for r in rows if r["kind"] == k]))
                                      for k in kinds if any(r["kind"] == k for r in rows)},
               "tokens_per_solve": float(sum(r["tokens"] for r in rows) / max(1, sum(r["task_success"] for r in rows))),
               "proposer_calls": int(sum(ag.proposer_calls for ag in self.agents.values())),
               "hub_gene_cycles": int(sum(1 for r in rows if r["source"] == "hub")),
               "poisoned_cycles": int(sum(1 for r in rows if r.get("gene_poisoned"))),
               "poisoned_in_stores": self.poisoned_in_stores(),
               "quarantined": int(sum(ag.n_quarantined for ag in self.agents.values())),
               "quarantine_rejected": int(sum(ag.n_quarantine_rejected for ag in self.agents.values())),
               "epochs": self.epochs}
        upl = [r["gene_true_uplift"] for r in rows if r.get("source") == "hub" and "gene_true_uplift" in r]
        out["hub_gene_true_uplift"] = float(np.mean(upl)) if upl else float("nan")
        if self.hub is not None:
            out["hub"] = self.hub_metrics()
            out["credit_share_by_kind"] = self.credits_by_kind()
        return out
