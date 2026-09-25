"""Configuration for the gene-hub method (``rsi.evomap``).

``mode`` switches between the two scoring regimes the spec asks for (§9.1
porting notes):

``faithful`` (EvoMap / Evolver v1.94.0 behaviour, for replication arms)
    faithful validation runner (missing scripts skipped, empty list ok, 2
    retries), process-score keep rule, memory-graph outcome recorded one cycle
    late and read from the executor's transcript first, hub hits used in
    ``reference`` mode (injected directly), distiller fallback validation
    ``python --version``, failure-distilled repair genes (empty validation).

``safe`` (our fixes, each logged in the ImprovementResult meta)
    safe runner (no silent skips, empty list FAILS), vacuity detector (lint +
    discriminative) and the agent's own graded task result in the keep rule,
    outcome = the measured solidify outcome recorded immediately, hub assets
    quarantined and re-tested on the agent's own held-out tasks before use
    (a rejected asset is never re-tested), no failure-distilled repair genes.

Any field left ``None`` takes the mode's default (see :meth:`Config.resolved`).
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Optional

MODE_DEFAULTS = {
    "faithful": {"outcome_source": "faithful", "outcome_timing": "next_cycle", "reuse_mode": "reference",
                 "hub_when": "always", "require_task_success": False, "vacuity_check": False,
                 "failed_capsule_rule": "absolute", "carry_log_signals": True, "skip_geneless_success": False,
                 "failure_distill": True, "reject_memory": False},
    "safe": {"outcome_source": "safe", "outcome_timing": "immediate", "reuse_mode": "quarantine",
             "hub_when": "no_local", "require_task_success": True, "vacuity_check": True,
             "failed_capsule_rule": "relative", "carry_log_signals": False, "skip_geneless_success": True,
             "failure_distill": False, "reject_memory": True},
}


@dataclass
class Config:
    # loop
    cycles: int = 30                         # T: local evolution cycles (one task each)
    mode: str = "safe"                       # faithful | safe
    seed: int = 0
    task_order: str = "shuffle"              # shuffle | cycle (over the decision split)
    split: str = "evolve"                    # decision split the agent practises on
    heldout_split: Optional[str] = "val"     # the agent's own held-out tasks (quarantine A/B); falls back to split
    success_threshold: float = 0.5           # graded score >= this counts as a solved task
    # selection / memory
    selector_mode: str = "current"           # current | spec   (§4.2)
    memory_mode: str = "current"             # current | spec   (§4.4)
    memory_key_match: str = "jaccard"        # spec mode: exact (sdk 1.1.0) | jaccard (spec text)
    use_memory: bool = True
    drift: bool = False                      # IS_RANDOM_DRIFT
    plateau_override: bool = True
    require_match: bool = True               # port deviation: irrelevant genes score 0
    strategy_preset: str = "balanced"
    failed_capsule_bans: bool = True
    epigenetic_suppression: bool = True       # marks <= -0.3 in this environment hard-suppress a gene (§4.5)
    failed_capsule_rule: Optional[str] = None   # absolute (engine: >= 2 failures) | relative (and > successes)
    carry_log_signals: Optional[bool] = None  # faithful: signals also come from the previous cycle's log/trace
    # outcome inference (None = mode default)
    outcome_source: Optional[str] = None     # faithful (transcript first) | safe (measured only)
    outcome_timing: Optional[str] = None     # next_cycle | immediate
    # solve
    propose: bool = True                     # write a new gene when nothing fits and the scratch attempt fails
    retry_after_propose: bool = True
    validation_hint: str = ""                # appended to the gene-writer prompt (e.g. " (e.g. python smoke_test.py)")
    default_validation: list = field(default_factory=list)
    # solidify
    require_task_success: Optional[bool] = None
    vacuity_check: Optional[bool] = None
    rollback: str = "stash"
    skip_geneless_success: Optional[bool] = None   # safe: a task solved with no gene has nothing to solidify
    env: str = "sim/py3.11"
    # distill
    distill: bool = True
    distill_every: int = 5
    distill_min_capsules: int = 10
    llm_distill: bool = False
    failure_distill: Optional[bool] = None   # faithful: repair genes from >= 5 failed capsules (empty validation);
    #                                          safe: off (such genes can never pass a discriminative keep rule)
    # hub
    publish: bool = True
    hub_when: Optional[str] = None           # always | no_local
    reuse_mode: Optional[str] = None         # reference | direct | quarantine
    hub_k: int = 5
    reuse_threshold: float = 0.72
    reuse_threshold_problem: float = 0.55
    publish_requires_eligibility: bool = True   # Evolver: score >= 0.78, streak >= 2, safe blast
    # quarantine / taskcheck
    quarantine_k: int = 2
    quarantine_n: int = 12
    quarantine_min_tasks: int = 3
    quarantine_gate: str = "rrsi"            # rrsi | lcb
    quarantine_delta: Optional[float] = None
    reject_memory: Optional[bool] = None     # safe: never re-test an asset this agent's quarantine already rejected
    #                                          (re-sampling held-out tasks until a useless asset passes = p-hacking)
    taskcheck_n: int = 4
    taskcheck_k: int = 1
    taskcheck_alpha: float = 0.2             # rsi-taskcheck passes iff the paired (1 - alpha) CI lower bound > 0
    # evaluation
    eval_k: int = 1
    workers: int = 1

    def resolved(self) -> "Config":
        c = dataclasses.replace(self)
        for k, v in MODE_DEFAULTS[self.mode].items():
            if getattr(c, k) is None:
                setattr(c, k, v)
        return c

    def to_json(self) -> dict:
        return dataclasses.asdict(self)
