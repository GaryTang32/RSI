"""Meta-Harness hyperparameters (defaults follow the paper and its release)."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional

#: What the proposer may read: the paper's Table 3 ablations plus the equal-budget baseline arms.
HISTORY_MODES = ("full", "scores_summary", "scores_only", "window", "last_only", "seed_only")


@dataclass
class Config:
    """Meta-Harness loop configuration (paper defaults: 20 iterations, k = 2, full history)."""

    iterations: int = 20
    k: int = 2                                 # candidates per iteration
    history_mode: str = "full"                 # window: OPRO-style; last_only: GEPA-style; seed_only: Best-of-N
    window: int = 5
    # Strip wall-clock fields from the files the proposer reads, so a run replays exactly from its LLM cache.
    deterministic_view: bool = True
    objectives: tuple[str, ...] = ("score", "context_cost")   # ("score",) = scalar objective
    cost_metric: str = "context_chars"         # all calls of a query | context_chars_last_call (release) | tokens
    search_split: str = "evolve"
    test_splits: tuple[str, ...] = ("test",)   # evaluated once, by finalize()
    trials: int = 1                            # seeds per unit
    reeval_incumbent: int = 0                  # > 0 (not in the paper): extra seeds for every new frontier _best
    reeval_max_per_iteration: int = 3
    tradeoff: str = ""                         # desired accuracy-context trade-off, stated to the proposer
    workers: int = 1
    eval_budget: Optional[int] = None          # max candidate evaluations
    leakage_screen: bool = False               # the paper's loop has no mechanical guard
    validate: bool = True
    validate_timeout_s: float = 30.0
    validate_in_subprocess: bool = True
    proposer_timeout_s: float = 2400.0
    finalize: bool = True
    summaries: str = "auto"                    # auto (only scores_summary) | always | never
    seed: int = 0
    trace: bool = True
    shadow_monitor: bool = True                # score every new frontier _best on sealed holdout/ood (trace only)
    shadow_splits: Optional[tuple[str, ...]] = None
    shadow_k: int = 1
    shadow_workers: int = 2
    notes: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.history_mode not in HISTORY_MODES:
            raise ValueError(f"history_mode must be one of {HISTORY_MODES}")

    def to_json(self) -> dict:
        return asdict(self)
