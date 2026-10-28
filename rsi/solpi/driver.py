"""SoL-Pi outer protocol on any Domain: oracle-ranked lineages, held-out firewall, composition of survivors."""
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
    """SoL-Pi protocol settings; ``gate`` is predeclared and frozen (its digest is recorded)."""

    gate: GateSpec = field(default_factory=GateSpec)
    n_lineages: int = 10             # breadth: only the top oracle-ranked ideas get rollouts
    max_iters: int = 4
    ralph_max: int = 3
    review_max: int = 2              # review rejection -> re-implementation rounds per iteration
    screen_split: str = "evolve"
    rollout_tasks_per_family: int = 2
    k: int = 1
    holdout_split: Optional[str] = "holdout"
    firewall: bool = True
    sweep: bool = True               # lineage keeps iterating after a pass; freezes the nondominated best-eta variant
    validate_composition: bool = False   # re-gate the composed stack; drop survivors greedily
    compose: bool = True
    rounds: int = 1
    workers: int = 1
    seed: int = 0
    trace: bool = True               # write-only audit trace whenever run() gets an out_dir
    # off by default: sealed splits are otherwise touched only by the firewall
    shadow_monitor: bool = False
    shadow_splits: Optional[tuple[str, ...]] = None
    shadow_k: int = 1
    shadow_workers: int = 2

    def to_json(self) -> dict:
        return {**asdict(self), "gate_digest": self.gate.digest()}


class AutoResearchDriver:
    def __init__(self, domain: Domain, seed_artifact: Artifact, *, llm_task: Optional[LLM], ideas: Sequence[Idea],
                 proposer: MechanismProposer, reviewer: Optional[Reviewer] = None, config: Config = Config(),
                 out_dir: Optional[str | Path] = None, monitor=None) -> None:
        self.domain, self.seed, self.llm = domain, seed_artifact, llm_task
        self.pool = IdeaPool(ideas)
        self.proposer = proposer
        self.cfg = config
        if config.firewall and not (config.holdout_split and domain.tasks.splits.get(config.holdout_split)):
            raise ValueError(f"SoL-Pi's held-out firewall needs a non-empty {config.holdout_split!r} split: add one "
                             "(e.g. TaskSuite fractions with 'holdout') or pass firewall=False to accept candidates on "
                             "the screen alone")
        self.gate = DualGate(config.gate)
        self.reviewer = reviewer or SmokeReviewer(domain, llm_task)
        self.out = Path(out_dir) if out_dir else Path(tempfile.mkdtemp(prefix="solpi_"))
        self.out.mkdir(parents=True, exist_ok=True)
        self.ledger = Ledger(self.out / "ledger.jsonl")
        self.ev = Evaluator(domain, llm_task, workers=config.workers)     # sealed splits raise
        from .tracing import make_tracer      # lazy: keeps ``tracing`` out of the package namespace
        self.tr = make_tracer(domain, self.out, enabled=bool(out_dir is not None and config.trace),
                              monitor=config.shadow_monitor if monitor is None else monitor, llm_task=llm_task,
                              splits=config.shadow_splits, k=config.shadow_k, workers=config.shadow_workers)

    def screen_tasks(self):
        fams = self.cfg.gate.families
        return [t for t in self.domain.tasks.split(self.cfg.screen_split) if fams is None or t.family in fams]

    def base_metrics(self, art: Artifact) -> tuple[Metrics, list]:
        r = self.ev.evaluate(art, self.screen_tasks(), k=self.cfg.k, label="screen")
        self._base_ev = r
        return metrics_from_eval(r), [t for trs in r.trials.values() for t in trs]

    def run_round(self, base: Artifact, rnd: int) -> dict:
        t0 = time.time()
        bm, trials = self.base_metrics(base)
        est = {i.id: oracle_estimate(i, trials) for i in self.pool.ideas}
        chosen = self.pool.select(self.cfg.n_lineages, est)
        self.tr.base(rnd, base, self._base_ev, bm, est, chosen)
        results: list[LineageResult] = []
        for idea in chosen:
            lin = Lineage(idea, evaluator=self.ev, gate=self.gate, proposer=self.proposer, reviewer=self.reviewer,
                          base=base, base_metrics=bm, screen_split=self.cfg.screen_split,
                          rollout_tasks_per_family=self.cfg.rollout_tasks_per_family, k=self.cfg.k,
                          max_iters=self.cfg.max_iters, ralph_max=self.cfg.ralph_max, ledger=self.ledger,
                          review_max=self.cfg.review_max, sweep=self.cfg.sweep,
                          tracer=self.tr if self.tr.enabled else None)
            results.append(lin.run())
        frozen = [r.frozen for r in results if r.frozen is not None]
        firewall_on = bool(self.cfg.firewall and self.cfg.holdout_split and
                           self.cfg.holdout_split in self.domain.tasks.splits)
        if firewall_on:
            fw = HoldoutFirewall(self.domain, self.llm, self.gate, base, split=self.cfg.holdout_split, k=self.cfg.k,
                                 sink_dir=self.out / f"firewall_r{rnd}", workers=self.cfg.workers)
            passed = {f.name: fw.evaluate_frozen(f.name, f.artifact) for f in frozen}
            heldout = fw.final_report()
        else:
            passed, heldout = {f.name: True for f in frozen}, []
        kept = [f for f in frozen if passed.get(f.name)]
        composed, conflicts = compose(base, [f.artifact for f in kept]) if (self.cfg.compose and kept) else (base, [])
        cm = metrics_from_eval(self.ev.evaluate(composed, self.screen_tasks(), k=self.cfg.k, label="screen")) \
            if kept else bm
        dropped = []
        if self.cfg.validate_composition and kept and self.cfg.compose:
            # small losses accumulate when mechanisms combine: drop the survivor whose removal helps most
            while kept and not self.gate.accept(bm, cm).accept:
                trials = []
                for f in kept:
                    rest = [g for g in kept if g is not f]
                    c2, _ = compose(base, [g.artifact for g in rest]) if rest else (base, [])
                    m2 = metrics_from_eval(self.ev.evaluate(c2, self.screen_tasks(), k=self.cfg.k, label="screen"))
                    trials.append((self.gate.accept(bm, m2).accept, m2.agg["score"], -m2.agg["cost"], f, c2, m2))
                *_, f, c2, m2 = max(trials, key=lambda x: (x[0], x[1], x[2]))
                kept.remove(f)
                dropped.append(f.idea.id)
                composed, cm = c2, m2
        self.tr.firewall_and_compose(rnd, base, frozen, passed, heldout, kept, composed, conflicts, bm, cm, dropped,
                                     firewall_on)
        usage = sum((r.usage for r in results), Usage())
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
            "dropped_by_composition_check": dropped,
            "composed_metrics": cm.to_json(), "conflicts": conflicts, "gate_digest": self.gate.digest,
            "heldout": heldout, "composed": composed, "usage": usage.to_dict(), "seconds": time.time() - t0}

    def run(self) -> ImprovementResult:
        self.tr.run_start(self)
        base = self.seed
        rounds = []
        for rnd in range(1, self.cfg.rounds + 1):
            r = self.run_round(base, rnd)
            rounds.append(r)
            if not r["survivors"]:
                break
            base = r["composed"]
        traj = [{k: v for k, v in r.items() if k not in ("composed", "heldout", "lineages")} for r in rounds]
        keys = ("calls", "input_tokens", "output_tokens", "cost_usd", "latency_s")
        tot = sum((Usage(**{k: r["usage"][k] for k in keys}) for r in rounds), Usage())
        usage = {"research": tot.to_dict(), "_total": tot.to_dict()}
        if self.llm is not None:
            usage["task"] = self.llm.meter.snapshot()
        res = ImprovementResult("solpi", self.seed, base, self.ledger, trajectory=traj, usage=usage,
                                stop_reason="rounds", out_dir=str(self.out),
                                meta={"rounds": [{k: v for k, v in r.items() if k != "composed"} for r in rounds],
                                      "config": self.cfg.to_json(), "gate_digest": self.gate.digest})
        if self.tr.monitor is not None and self.tr.monitor.ev.llm is not None:
            usage["shadow_monitor"] = self.tr.monitor.ev.llm.meter.snapshot()
        self.tr.run_end({"rounds": len(rounds), "survivors": [r["survivor_ideas"] for r in rounds],
                         "best": res.best.short_id, "usage": usage, "stop_reason": res.stop_reason})
        (self.out / "rounds.json").write_text(json.dumps(res.meta["rounds"], indent=1, default=str))
        return res


def run(domain: Domain, seed_artifact: Artifact, *, llm_task: Optional[LLM] = None,
        llm_propose: Optional[LLM] = None, config: Optional[Config] = None, out_dir: Optional[str | Path] = None,
        ideas: Optional[Sequence[Idea]] = None, proposer: Optional[MechanismProposer] = None,
        reviewer: Optional[Reviewer] = None, monitor=None) -> ImprovementResult:
    """Run SoL-Pi on any Domain; ``llm_propose=None`` uses the offline proposer (agentworld / agentqa)."""
    from .mocks import AGENTQA_IDEAS, AGENTWORLD_IDEAS, AgentQAEditProposer, LibraryProposer, LLMMechanismProposer
    cfg = config or Config()
    name = getattr(domain, "name", "")
    from ..domains.agentqa import AgentQADomain
    if isinstance(domain, AgentQADomain):
        name = "agentqa"                # AgentQA-layout domains (e.g. BBH) share its offline ideas and proposer
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
                             reviewer=reviewer, config=cfg, out_dir=out_dir, monitor=monitor)
    return drv.run()
