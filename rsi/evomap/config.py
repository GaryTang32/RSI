"""Config for ``rsi.evomap``; ``mode`` picks the faithful (Evolver v1.94.0) or safe defaults for ``None`` fields."""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Optional

MODE_DEFAULTS = {
    "faithful": {"outcome_source": "faithful", "outcome_timing": "next_cycle", "reuse_mode": "reference",
                 "hub_when": "first", "new_gene_retries": 1, "new_gene_check": "single",
                 "require_task_success": False, "vacuity_check": False,
                 "failed_capsule_rule": "absolute", "carry_log_signals": True, "skip_geneless_success": False,
                 "failure_distill": True, "reject_memory": False, "require_match": False,
                 "llm_signal_layer": True},
    "safe": {"outcome_source": "safe", "outcome_timing": "immediate", "reuse_mode": "quarantine",
             "hub_when": "no_local", "new_gene_retries": 3, "new_gene_check": "paired",
             "require_task_success": True, "vacuity_check": True,
             "failed_capsule_rule": "relative", "carry_log_signals": False, "skip_geneless_success": True,
             "failure_distill": False, "reject_memory": True, "require_match": True,
             "llm_signal_layer": False},
}


@dataclass
class Config:
    # loop
    cycles: int = 30
    mode: str = "safe"                       # faithful | safe
    seed: int = 0
    task_order: str = "shuffle"              # shuffle | cycle
    split: str = "evolve"                    # decision split
    heldout_split: Optional[str] = "val"     # quarantine A/B tasks; falls back to split
    success_threshold: float = 0.5
    # selection / memory
    selector_mode: str = "current"           # current | spec (§4.2)
    memory_mode: str = "current"             # current | spec (§4.4)
    memory_key_match: str = "jaccard"        # spec mode: exact | jaccard
    use_memory: bool = True
    drift: bool = False
    plateau_override: bool = True
    require_match: Optional[bool] = None     # True: a gene with no pattern hit and no token overlap scores 0
    strategy_preset: str = "balanced"
    failed_capsule_bans: bool = True
    epigenetic_suppression: bool = True      # marks <= -0.3 here hard-suppress a gene (§4.5)
    failed_capsule_rule: Optional[str] = None   # absolute (>= 2 failures) | relative (and > successes)
    carry_log_signals: Optional[bool] = None    # signals also from the previous cycle's trace
    llm_signal_layer: Optional[bool] = None     # Evolver layer 3: hub signal analyzer every 5th cycle
    # outcome inference
    outcome_source: Optional[str] = None     # faithful (transcript first) | safe (measured only)
    outcome_timing: Optional[str] = None     # next_cycle | immediate
    # solve
    propose: bool = True
    retry_after_propose: bool = True
    new_gene_retries: Optional[int] = None   # fresh-seed retries of a new gene on its own task
    new_gene_check: Optional[str] = None     # single (> half solve) | paired (and beats the bare harness)
    validation_hint: str = ""                # appended to the gene-writer prompt
    default_validation: list = field(default_factory=list)
    # solidify
    require_task_success: Optional[bool] = None
    vacuity_check: Optional[bool] = None
    rollback: str = "stash"
    estimate_drift_penalty: bool = False     # Evolver never applies it (dead key, §4.10)
    skip_geneless_success: Optional[bool] = None
    env: str = "sim/py3.11"
    # distill
    distill: bool = True
    distill_every: int = 5
    distill_min_capsules: int = 10
    llm_distill: bool = False
    failure_distill: Optional[bool] = None   # repair genes from >= 5 failed capsules
    # hub
    publish: bool = True
    hub_when: Optional[str] = None           # first | always | no_local
    reuse_mode: Optional[str] = None         # reference | direct | replace | quarantine
    hub_k: int = 5
    reuse_threshold: float = 0.72
    reuse_threshold_problem: float = 0.55
    publish_requires_eligibility: bool = True   # score >= 0.78, streak >= 2, safe blast
    # quarantine / taskcheck
    quarantine_k: int = 2
    quarantine_n: int = 12
    quarantine_min_tasks: int = 3
    quarantine_gate: str = "rrsi"            # rrsi | lcb
    quarantine_delta: Optional[float] = None
    reject_memory: Optional[bool] = None     # never re-test an asset the quarantine rejected
    taskcheck_n: int = 4
    taskcheck_k: int = 1
    taskcheck_alpha: float = 0.2             # pass iff the paired (1 - alpha) CI lower bound > 0
    # evaluation
    eval_k: int = 1
    workers: int = 1
    # tracing (write-only)
    trace: bool = True
    trace_baseline_k: int = 2                # 0 = skip the seed baseline
    shadow_monitor: bool = True              # score new library versions on holdout / ood (audit only)
    monitor_k: int = 1
    monitor_workers: int = 2

    def resolved(self) -> "Config":
        c = dataclasses.replace(self)
        for k, v in MODE_DEFAULTS[self.mode].items():
            if getattr(c, k) is None:
                setattr(c, k, v)
        return c

    def to_json(self) -> dict:
        return dataclasses.asdict(self)
