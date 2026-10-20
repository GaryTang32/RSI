"""GEPA hyperparameters, defaulting to the paper / ``gepa.optimize`` values."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional


@dataclass
class Config:
    # budget / splits
    max_metric_calls: Optional[int] = 500        # B (rollouts); None -> other stoppers only
    budget_mode: str = "reference_soft"          # reference_soft: checked between iterations | hard: mid-iteration
    train_split: str = "evolve"                  # D_feedback; falls back to "train"
    val_split: Optional[str] = "val"             # D_pareto; None or missing -> D_train
    components: Optional[list[str]] = None       # None -> text files, not *.py

    # the loop
    minibatch_size: int = 3
    candidate_selection: str = "pareto"          # pareto | current_best | epsilon_greedy | top_k_pareto | beam_search
    frontier_type: str = "instance"              # instance | objective | hybrid | cartesian
    module_selector: str = "round_robin"         # round_robin | all
    acceptance: str = "strict_improvement"       # strict_improvement | improvement_or_equal | noise_margin
    noise_margin: float = 0.0
    epsilon: float = 0.1
    top_k: int = 5
    beam_n: int = 4
    skip_perfect_score: bool = True
    perfect_score: Optional[float] = None        # None -> domain.score_range[1]

    # merge (GEPA+Merge)
    use_merge: bool = False
    max_merge_invocations: int = 5
    merge_val_overlap_floor: int = 5
    merge_subsample_size: int = 5
    merge_cap_mode: str = "reference_soft"       # reference_soft | hard (paper App. G.4) | accepted
    merge_zero_weight: str = "uniform"           # uniform | raise (the reference's crash)
    merge_start_frac: float = 0.0                # no merge scheduled before this budget fraction (paper Obs. 5)

    # reflection
    reflection_template: Optional[str] = None    # None/"default" | "optimize_anything" | custom text
    reflection_system: Optional[str] = None
    feedback: str = "full"                       # full | score_only | none

    # evaluation
    val_seed: int = 0
    cache_evaluation: bool = False               # charge D_pareto re-evaluations of a candidate once
    workers: int = 1
    seed: int = 0

    # extra stoppers
    max_iterations: Optional[int] = None
    stop_at_score: Optional[float] = None
    no_improvement_patience: Optional[int] = None
    max_candidate_proposals: Optional[int] = None
    max_reflection_cost: Optional[float] = None
    timeout_s: Optional[float] = None
    max_consecutive_infra_failures: Optional[int] = 3   # a sustained outage would otherwise drain the budget

    # bookkeeping
    save_every: int = 1
    diff_chars: int = 4000
    extra: dict = field(default_factory=dict)

    # audit trace (<out_dir>/trace.jsonl, never read by the loop)
    trace: bool = True
    trace_max_text: int = 20000
    shadow_monitor: bool = True                  # score each new incumbent on sealed splits, into the trace only
    shadow_splits: Optional[list[str]] = None    # None -> sealed holdout/ood, else sealed test
    shadow_k: int = 1
    shadow_workers: int = 4

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, d: dict) -> "Config":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    def validate(self) -> None:
        if self.minibatch_size < 1:
            raise ValueError("minibatch_size must be >= 1")
        if self.budget_mode not in ("reference_soft", "hard"):
            raise ValueError("budget_mode must be 'reference_soft' or 'hard'")
        if self.module_selector not in ("round_robin", "all"):
            raise ValueError("module_selector must be 'round_robin' or 'all'")
        if self.merge_cap_mode not in ("reference_soft", "hard", "accepted"):
            raise ValueError("merge_cap_mode must be 'reference_soft', 'hard' or 'accepted'")
        if not 0.0 <= self.merge_start_frac < 1.0:
            raise ValueError("merge_start_frac must be in [0, 1)")
        if self.merge_start_frac > 0 and self.max_metric_calls is None:
            raise ValueError("merge_start_frac > 0 needs max_metric_calls (it is a fraction of the rollout budget)")
        if self.feedback not in ("full", "score_only", "none"):
            raise ValueError("feedback must be 'full', 'score_only' or 'none'")
