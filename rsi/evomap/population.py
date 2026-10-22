"""Population simulation: many agents of mixed kinds sharing one hub (spec §9.1, §9.3 Tier 1).

Kinds: ``honest`` (full cycles), ``inflator`` (honest content, inflated claims), ``freerider`` (never publishes),
``farmer`` (mass-publishes vacuous variants) and ``poisoner`` (harmful genes with discriminative-looking proof).
"""
from __future__ import annotations

import copy
import random
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Callable, Optional, Protocol, Sequence

import numpy as np

from rsi.core import Artifact, Domain, LLM

from .agent import AgentNode, Behavior
from .assets import Capsule, Clock, EvolutionEvent, Gene
from .config import Config
from .hashing import asset_id
from .hub import Bundle, register_agent
from .inject import Injector
from .metrics import ReuseMetrics
from .solidify import diff_text
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
            score: float = 0.99,
            fields: Sequence[str] = ("blast", "confidence", "streak", "score", "report")) -> Bundle:
    """Rewrite a bundle's claimed metadata and re-stamp the capsule id so it still verifies."""
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


def forged_bundle(g: Gene, author: str, n: int, summary: str, before: dict, after: dict, claims: dict, report: dict,
                  **capsule_extra) -> Bundle:
    """Stamp ``g`` and wrap it with a capsule and event carrying the ``claims`` metadata (adversary forges)."""
    g.stamp()
    blast = {"files": claims["files"], "lines": claims["lines"]}
    outcome = {"status": "success", "score": claims["score"]}
    cap = Capsule(id=f"capsule_{author}_{n:06d}", gene=g.id, trigger=list(g.signals_match), summary=summary,
                  confidence=claims["confidence"], blast_radius=blast, outcome=outcome,
                  success_streak=claims["streak"], content="\n".join(g.strategy), diff=diff_text(before, after),
                  strategy=list(g.strategy),
                  execution_trace=[{"step": 1, "stage": "build", "cmd": "apply", "exit": 0},
                                   {"step": 2, "stage": "validate", "cmd": (g.validation or ["-"])[0], "exit": 0}],
                  **capsule_extra).stamp()
    ev = EvolutionEvent(id=f"evt_{author}_{n:06d}", intent="repair", signals=list(g.signals_match), genes_used=[g.id],
                        mutation_id=f"mut_{n}", capsule_id=cap.id, blast_radius=dict(blast),
                        outcome=dict(outcome)).stamp()
    return Bundle(gene=g.to_dict(), capsule=cap.to_dict(), event=ev.to_dict(), report=report, pre_state=before,
                  post_state=after, meta={"author": author, "forged": True})


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
                 seed: int = 0, clock_step_s: float = 3600.0, bounties=None) -> None:
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
                    register_agent(hub, sp.name, sp.cluster)
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
        self.bounties = bounties            # optional economy.BountyDriver (X19)

    def _bounty_solvers(self) -> tuple:
        return tuple(k for k in getattr(self.bounties, "claimant_kinds", ("honest", "inflator"))
                     if k not in ("farmer", "poisoner"))

    def _task(self, sp: AgentSpec, rng: random.Random):
        return rng.choice([t for t in self.tasks if sp.classes is None or t.family in sp.classes])

    def _adversary_turn(self, sp: AgentSpec, rng: random.Random, ep: int) -> None:
        if self.hub is None or self.forge is None:
            return
        make = self.forge.farm_bundle if sp.kind == "farmer" else self.forge.poison_bundle
        fresh = []
        for _ in range(sp.farm_rate):
            b = make(sp.name, rng, ep)
            d = self.hub.publish(b, sp.name)
            self.cycles.append({"epoch": ep, "agent": sp.name, "kind": sp.kind, "published": d.status})
            if b.capsule:
                fresh.append(b.capsule.get("asset_id") or b.capsule.get("id"))
        if self.bounties is not None and sp.kind in getattr(self.bounties, "claimant_kinds", ()):
            self.bounties.farmer_turn(self, sp, fresh, ep)

    def run(self, epochs: int, *, metrics_every: int = 0) -> dict:
        for ep in range(epochs):
            order = list(self.specs)
            self.rng.shuffle(order)
            if self.bounties is not None:
                self.bounties.on_epoch_start(self, ep, random.Random(f"{self.seed}-bounties-{ep}"))
            for sp in order:
                arng = random.Random(f"{self.seed}-{sp.name}-{ep}")
                if sp.kind in ("farmer", "poisoner"):
                    self._adversary_turn(sp, arng, ep)
                    continue
                ag = self.agents[sp.name]
                bounty = self.bounties is not None and sp.kind in self._bounty_solvers()
                for _ in range(sp.tasks_per_epoch):
                    task = self.bounties.task_for(self, sp, ag, arng) if bounty else None
                    cr = ag.cycle(task if task is not None else self._task(sp, arng))
                    if bounty:
                        self.bounties.after_cycle(self, sp, ag, cr, ep)
                    row = {"epoch": ep, "agent": sp.name, "kind": sp.kind, "ability": sp.ability, **cr.to_json()}
                    # every gene injected into the solve, stored or not (a reference-mode hub gene never is)
                    used = ag.last_gene
                    if self.truth is not None and used is not None:
                        row["gene_true_uplift"] = self.truth(used)
                    injected = list(getattr(ag, "last_genes", []) or ([used] if used is not None else []))
                    if self.forge is not None and injected:
                        row["gene_poisoned"] = any(self.forge.is_poisoned(g) for g in injected)
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
        return ReuseMetrics(truth=self.truth, agents=[sp.name for sp in self.specs]).compute(self.hub)

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

    def _from_hub(self, g: Gene, owner: Optional[str] = None) -> bool:
        """A stored gene from the hub: promoted from quarantine, parented on a hub asset, or authored by another."""
        prov = g.provenance or {}
        if prov.get("kind") == "external":
            return True
        if owner is not None and prov.get("author") not in (None, owner):
            return True
        return self.hub is not None and bool(g.parent) and g.parent in getattr(self.hub, "records", {})

    def poisoned_in_stores_split(self) -> dict:
        """Harmful genes an agent wrote itself (``self``) vs took from the hub (``hub``)."""
        out = {"hub": 0, "self": 0}
        if self.forge is None:
            return out
        for ag in self.agents.values():
            for g in ag.store.genes.values():
                if self.forge.is_poisoned(g):
                    out["hub" if self._from_hub(g, ag.name) else "self"] += 1
        return out

    def summary(self) -> dict:
        rows = [r for r in self.cycles if "task_success" in r]
        kinds = Counter(sp.kind for sp in self.specs)
        split = self.poisoned_in_stores_split()
        out = {"n_agents": len(self.specs), "kinds": dict(kinds), "n_cycles": len(rows),
               "solve_rate": float(np.mean([r["task_success"] for r in rows])) if rows else float("nan"),
               "solve_rate_by_kind": {k: float(np.mean([r["task_success"] for r in rows if r["kind"] == k]))
                                      for k in kinds if any(r["kind"] == k for r in rows)},
               "tokens_per_solve": float(sum(r["tokens"] for r in rows) / max(1, sum(r["task_success"] for r in rows))),
               "proposer_calls": int(sum(ag.proposer_calls for ag in self.agents.values())),
               "hub_gene_cycles": int(sum(1 for r in rows if r["source"] == "hub")),
               "poisoned_cycles": int(sum(1 for r in rows if r.get("gene_poisoned"))),
               "poisoned_in_stores": self.poisoned_in_stores(),
               "poisoned_in_stores_hub": split["hub"], "poisoned_in_stores_self": split["self"],
               "quarantined": int(sum(ag.n_quarantined for ag in self.agents.values())),
               "quarantine_rejected": int(sum(ag.n_quarantine_rejected for ag in self.agents.values())),
               "epochs": self.epochs}
        upl = [r["gene_true_uplift"] for r in rows if r.get("source") == "hub" and "gene_true_uplift" in r]
        out["hub_gene_true_uplift"] = float(np.mean(upl)) if upl else float("nan")
        if self.hub is not None:
            out["hub"] = self.hub_metrics()
            out["credit_share_by_kind"] = self.credits_by_kind()
        return out
