"""GEPA hyperparameters with the paper / ``gepa.optimize`` defaults (spec 4.6)."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional


@dataclass
class Config:
    """All knobs of :func:`rsi.gepa.run`.

    Defaults follow ``gepa.optimize`` / the paper: minibatch b = 3, Pareto candidate
    selection over per-instance frontier keys, round-robin module selection, strict
    minibatch improvement, merge off (``use_merge=True`` = "GEPA+Merge", at most 5
    merges, 5-id subsample, overlap floor 5), perfect-score skip on (perfect = the
    domain's maximum score, i.e. 1.0 for [0, 1] metrics), seed 0.
    """

    # budget / splits
    max_metric_calls: Optional[int] = 500        # B (rollouts); None -> other stoppers only
    budget_mode: str = "reference_soft"          # "reference_soft": checked between iterations (overshoot <= 2b+|V|)
    #                                              "hard": optimize_anything eval-server cap, stops mid-iteration
    train_split: str = "evolve"                  # D_feedback (minibatches); falls back to "train"
    val_split: Optional[str] = "val"             # D_pareto; None or missing -> D_train (multi-task mode)
    components: Optional[list[str]] = None       # evolvable files; None -> auto (text files, not *.py)

    # the loop
    minibatch_size: int = 3
    candidate_selection: str = "pareto"          # pareto | current_best | epsilon_greedy | top_k_pareto | beam_search
    frontier_type: str = "instance"              # instance | objective | hybrid | cartesian
    module_selector: str = "round_robin"         # round_robin | all
    acceptance: str = "strict_improvement"       # strict_improvement | improvement_or_equal | noise_margin
    noise_margin: float = 0.0                    # delta for acceptance="noise_margin"
    epsilon: float = 0.1
    top_k: int = 5
    beam_n: int = 4
    skip_perfect_score: bool = True
    perfect_score: Optional[float] = None        # None -> domain.score_range[1] (1.0 for [0, 1] scores, the
    #                                              gepa.optimize default)

    # merge (GEPA+Merge)
    use_merge: bool = False
    max_merge_invocations: int = 5
    merge_val_overlap_floor: int = 5
    merge_subsample_size: int = 5
    merge_cap_mode: str = "reference_soft"       # reference_soft | hard
    merge_zero_weight: str = "uniform"           # uniform (fix) | raise (reference crash)

    # reflection
    reflection_template: Optional[str] = None    # None/"default" = paper meta-prompt | "optimize_anything" | custom
    reflection_system: Optional[str] = None
    feedback: str = "full"                       # full | score_only (ScoreOnlyReflection) | none

    # evaluation
    val_seed: int = 0                            # rollout seed of every D_pareto evaluation (one draw per candidate)
    cache_evaluation: bool = False               # count D_pareto re-evaluations of the same candidate only once
    workers: int = 1                             # parallel rollouts inside one evaluation
    seed: int = 0                                # the shared random.Random (selector, sampler, merge)

    # extra stoppers
    max_iterations: Optional[int] = None
    stop_at_score: Optional[float] = None
    no_improvement_patience: Optional[int] = None
    max_candidate_proposals: Optional[int] = None
    max_reflection_cost: Optional[float] = None
    timeout_s: Optional[float] = None

    # bookkeeping
    save_every: int = 1                          # write state.json every N iterations (resume granularity)
    diff_chars: int = 4000                       # ledger diff truncation
    extra: dict = field(default_factory=dict)

    # audit trace (rsi.trace format, <out_dir>/trace.jsonl; write-only: never read by the loop)
    trace: bool = True                           # on whenever out_dir is given
    trace_max_text: int = 20000                  # per-string clip in trace.jsonl (prompts, replies, diffs)
    shadow_monitor: bool = True                  # score each new incumbent on sealed splits, into the trace only
    shadow_splits: Optional[list[str]] = None    # None -> sealed holdout/ood of the domain, else its sealed test
    shadow_k: int = 1
    shadow_workers: int = 4

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, d: dict) -> "Config":
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**known)

    def validate(self) -> None:
        if self.minibatch_size < 1:
            raise ValueError("minibatch_size must be >= 1")
        if self.budget_mode not in ("reference_soft", "hard"):
            raise ValueError("budget_mode must be 'reference_soft' or 'hard'")
        if self.module_selector not in ("round_robin", "all"):
            raise ValueError("module_selector must be 'round_robin' or 'all'")
        if self.merge_cap_mode not in ("reference_soft", "hard"):
            raise ValueError("merge_cap_mode must be 'reference_soft' or 'hard'")
        if self.feedback not in ("full", "score_only", "none"):
            raise ValueError("feedback must be 'full', 'score_only' or 'none'")
