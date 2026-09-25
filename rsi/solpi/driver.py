"""AutoResearchDriver: SoL-Pi's outer protocol (spec B3.1) on any rsi.core Domain.

::

    CAP/EFF predeclared (GateSpec, digest recorded)          # outside the optimiser's control
    base metrics on the training screen (all families)
    oracle analysis on base trajectories -> rank the idea pool; select n_lineages (breadth)
    for each idea: Lineage(...).run()  -> frozen candidate or nothing   (depth, training split only)
    for each frozen candidate: HoldoutFirewall.evaluate_frozen()       (one-way; failures reject silently)
    keep nondominated survivors; compose them into one harness (opt-in mechanisms)
    optional rounds > 1: the composed harness becomes the next base  ("preliminary" in the sources)
"""
from __future__ import annotations

import json
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional, Sequence

from ..core.artifact import Artifact
from ..core.domain import Domain
from ..core.evaluate import Evaluator
from ..core.ledger import Ledger, Node
from ..core.llm import LLM, Usage
from ..core.run import ImprovementResult
from .gate import DualGate, GateSpec, HoldoutFirewall, Metrics, metrics_from_eval
from .research import Idea, IdeaPool, Lineage, LineageResult, MechanismProposer, Reviewer, SmokeReviewer, compose, \
    oracle_estimate


@dataclass
class Config:
    """SoL-Pi research-protocol configuration.

    The gate (``gate``) is predeclared and frozen. ``n_lineages`` ideas are run
    (breadth), each for at most ``max_iters`` propose/implement/review/validate
    cycles (depth) with a Ralph loop of ``ralph_max`` repairs. Validation uses the
    training ``screen_split`` (restricted to ``gate.families`` for single-environment
    protocols) with ``k`` trials per task; ``holdout_split`` is used only by the
    firewall. ``sweep=True`` makes each lineage evaluate its whole variant grid and
    keep a nondominated passing variant (the ObservationPack sweep); otherwise a
    lineage freezes its first gate-passing candidate. All firewall survivors of
    different lineages are composed (they are independent opt-in mechanisms).
    """

    gate: GateSpec = field(default_factory=GateSpec)
    n_lineages: int = 10
    max_iters: int = 4
    ralph_max: int = 3
    screen_split: str = "evolve"
    rollout_tasks_per_family: int = 2
    k: int = 1
    holdout_split: Optional[str] = "holdout"
    firewall: bool = True
    sweep: bool = False              # lineage evaluates every variant and keeps the nondominated passing one
    compose: bool = True
    rounds: int = 1
    workers: int = 1
    seed: int = 0

    def to_json(self) -> dict:
        d = asdict(self)
        d["gate_digest"] = self.gate.digest()
        return d


class AutoResearchDriver:
    def __init__(self, domain: Domain, seed_artifact: Artifact, *, llm_task: Optional[LLM], ideas: Sequence[Idea],
                 proposer: MechanismProposer, reviewer: Optional[Reviewer] = None, config: Config = Config(),
                 out_dir: Optional[str | Path] = None) -> None:
        self.domain, self.seed, self.llm = domain, seed_artifact, llm_task
        self.pool = IdeaPool(ideas)
        self.proposer = proposer
        self.cfg = config
        self.gate = DualGate(config.gate)
        self.reviewer = reviewer or SmokeReviewer(domain, llm_task)
        self.out = Path(out_dir) if out_dir else Path(tempfile.mkdtemp(prefix="solpi_"))
        self.out.mkdir(parents=True, exist_ok=True)
        self.ledger = Ledger(self.out / "ledger.jsonl")
        # the lineages' evaluator can only see decision splits (sealed splits raise)
        self.ev = Evaluator(domain, llm_task, workers=config.workers)

    def screen_tasks(self):
        fams = self.cfg.gate.families
        return [t for t in self.domain.tasks.split(self.cfg.screen_split) if fams is None or t.family in fams]

    def base_metrics(self, art: Artifact) -> tuple[Metrics, list]:
        r = self.ev.evaluate(art, self.screen_tasks(), k=self.cfg.k, label="screen")
        return metrics_from_eval(r), [t for trs in r.trials.values() for t in trs]

    def run_round(self, base: Artifact, rnd: int) -> dict:
        t0 = time.time()
        bm, trials = self.base_metrics(base)
        est = {i.id: oracle_estimate(i, trials) for i in self.pool.ideas}
        chosen = self.pool.select(self.cfg.n_lineages, est)
        results: list[LineageResult] = []
        for idea in chosen:
            lin = Lineage(idea, evaluator=self.ev, gate=self.gate, proposer=self.proposer, reviewer=self.reviewer,
                          base=base, base_metrics=bm, screen_split=self.cfg.screen_split,
                          rollout_tasks_per_family=self.cfg.rollout_tasks_per_family, k=self.cfg.k,
                          max_iters=self.cfg.max_iters, ralph_max=self.cfg.ralph_max, ledger=self.ledger,
                          sweep=self.cfg.sweep)
            results.append(lin.run())
        frozen = [r.frozen for r in results if r.frozen is not None]
        passed = {}
        if self.cfg.firewall and self.cfg.holdout_split and self.cfg.holdout_split in self.domain.tasks.splits:
            fw = HoldoutFirewall(self.domain, self.llm, self.gate, base, split=self.cfg.holdout_split, k=self.cfg.k,
                                 sink_dir=self.out / f"firewall_r{rnd}", workers=self.cfg.workers)
            for f in frozen:
                passed[f.name] = fw.evaluate_frozen(f.name, f.artifact)
            heldout = fw.final_report()
        else:
            passed = {f.name: True for f in frozen}
            heldout = []
        kept = [f for f in frozen if passed.get(f.name)]
        composed, conflicts = compose(base, [f.artifact for f in kept]) if (self.cfg.compose and kept) else (base, [])
        cm = metrics_from_eval(self.ev.evaluate(composed, self.screen_tasks(), k=self.cfg.k, label="screen")) \
            if kept else bm
        usage = Usage()
        for r in results:
            usage = usage + r.usage
        for f in frozen:
            self.ledger.add(Node(id=f"sp_frozen_r{rnd}_{f.idea.id}", parent=None, round=rnd, kind="frozen",
                                 status="survivor" if f in kept else "heldout_rejected",
                                 score=f.metrics.agg["score"], cost=f.metrics.agg["tokens"], change=f.name,
                                 artifact_id=f.artifact.id, metrics=dict(f.metrics.agg)))
        return {
            "round": rnd, "base_metrics": bm.to_json(), "oracle": est, "chosen": [i.id for i in chosen],
            "lineages": [{"idea": r.idea.id, "kind": r.idea.kind, "title": r.idea.title,
                          "frozen": r.frozen is not None, "iterations": r.iterations,
                          "n_iterations": len(r.iterations)} for r in results],
            "frozen": [f.name for f in frozen], "frozen_ideas": [f.idea.id for f in frozen],
            "heldout_passed": {k: bool(v) for k, v in passed.items()},
            "survivors": [f.name for f in kept], "survivor_ideas": [f.idea.id for f in kept],
            "composed_metrics": cm.to_json(), "conflicts": conflicts, "gate_digest": self.gate.digest,
            "heldout": heldout, "composed": composed, "usage": usage.to_dict(), "seconds": time.time() - t0}

    def run(self) -> ImprovementResult:
        base = self.seed
        rounds = []
        for rnd in range(1, self.cfg.rounds + 1):
            r = self.run_round(base, rnd)
            rounds.append(r)
            if not r["survivors"]:
                break
            base = r["composed"]
        traj = [{k: v for k, v in r.items() if k not in ("composed", "heldout", "lineages")} for r in rounds]
        usage = {"research": {}, "_total": {}}
        if self.llm is not None:
            usage["task"] = self.llm.meter.snapshot()
        tot = Usage()
        for r in rounds:
            tot = tot + Usage(**{k: r["usage"][k] for k in ("calls", "input_tokens", "output_tokens", "cost_usd",
                                                            "latency_s")})
        usage["research"] = tot.to_dict()
        usage["_total"] = tot.to_dict()
        res = ImprovementResult("solpi", self.seed, base, self.ledger, trajectory=traj, usage=usage,
                                stop_reason="rounds", out_dir=str(self.out),
                                meta={"rounds": [{k: v for k, v in r.items() if k != "composed"} for r in rounds],
                                      "config": self.cfg.to_json(), "gate_digest": self.gate.digest})
        (self.out / "rounds.json").write_text(json.dumps(res.meta["rounds"], indent=1, default=str))
        return res


def run(domain: Domain, seed_artifact: Artifact, *, llm_task: Optional[LLM] = None,
        llm_propose: Optional[LLM] = None, config: Optional[Config] = None, out_dir: Optional[str | Path] = None,
        ideas: Optional[Sequence[Idea]] = None, proposer: Optional[MechanismProposer] = None,
        reviewer: Optional[Reviewer] = None) -> ImprovementResult:
    """Run the SoL-Pi research protocol on any :class:`rsi.core.Domain`.

    Parameters
    ----------
    domain, seed_artifact:
        the environments (task families in the ``evolve`` split; held-out tasks in
        ``holdout``) and the base harness.
    llm_task:
        the agent backend / frozen model used for rollouts.
    llm_propose:
        LLM for the research roles (mechanism proposer/implementer); ``None`` uses
        the offline proposer for the domain (``agentworld`` / ``agentqa``).
    ideas:
        the idea pool (defaults exist for ``agentworld`` and ``agentqa``).
    config:
        :class:`Config` - notably the predeclared ``gate`` (:class:`GateSpec`).

    Returns an :class:`rsi.core.ImprovementResult` whose ``best`` is the composed
    harness of the (nondominated) survivors; ``meta["rounds"]`` records every
    lineage, gate result and the firewall's held-out verdicts.
    """
    from .mocks import AGENTQA_IDEAS, AGENTWORLD_IDEAS, AgentQAEditProposer, LibraryProposer, LLMMechanismProposer
    cfg = config or Config()
    name = getattr(domain, "name", "")
    if ideas is None:
        ideas = AGENTWORLD_IDEAS if name == "agentworld" else AGENTQA_IDEAS if name == "agentqa" else None
        if ideas is None:
            raise ValueError("pass ideas=[Idea(...), ...] for this domain")
    if proposer is None:
        if llm_propose is not None:
            proposer = LLMMechanismProposer(llm_propose)
        elif name == "agentworld":
            proposer = LibraryProposer()
        elif name == "agentqa":
            proposer = AgentQAEditProposer()
        else:
            raise ValueError("pass llm_propose=... or proposer=... for this domain")
    drv = AutoResearchDriver(domain, seed_artifact, llm_task=llm_task, ideas=ideas, proposer=proposer,
                             reviewer=reviewer, config=cfg, out_dir=out_dir)
    return drv.run()
