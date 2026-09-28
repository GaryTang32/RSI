"""Meta-Harness hyperparameters (defaults follow the paper / release; see spec A4)."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional

#: What the proposer may read (spec A9.1 ``ExperienceStore.view`` + the M2 baseline arms).
HISTORY_MODES = ("full", "scores_summary", "scores_only", "window", "last_only", "seed_only")


@dataclass
class Config:
    """Meta-Harness loop configuration.

    Paper defaults: text classification N = 20 iterations, k = 2 candidates per
    iteration (the release's skill asks for 3, TB2 for 1), full uncompressed
    history, no leakage guard, search split only during evolution, test once at
    finalisation. ``history_mode`` other than ``"full"`` gives the paper's Table 3
    ablations (``scores_only``, ``scores_summary``) and the M2 baseline arms
    (``window`` = OPRO-style last-w (code, score) pairs; ``last_only`` =
    GEPA/TextGrad-style single-candidate reflection on the current best's traces;
    ``seed_only`` = Best-of-N independent samples from the seed: the view shows only the run's seed
    harness, no scores and no other candidates).

    ``cost_metric``: ``"context_chars"`` (default) = injected context summed over ALL model calls per query
    (MemoClassify; audit N2 - the release's last-call measurement can be gamed by a tiny final call);
    ``"context_chars_last_call"`` = the release's ``inner_loop.py`` measurement; ``"tokens"`` = total tokens.

    ``tradeoff``: the desired accuracy-context trade-off, stated to the proposer ("given only the current
    metrics and the desired trade-off, the proposer is able to discover harnesses across ... the frontier",
    paper §4.1). The objective statement derived from ``objectives`` is always included; ``tradeoff`` adds
    a preference (e.g. "prefer harnesses under 3,000 context chars").

    ``reeval_incumbent`` (OFF by default = faithful: the paper and release score each candidate once with one
    seed and have no noise band, spec A8.4): when > 0, every system that becomes the frontier's ``_best`` is
    re-evaluated on ``trials + reeval_incumbent`` seeds and its stored scores become the pooled mean, before
    the frontier is recomputed. A challenger whose single-seed score was lucky then falls back behind the
    (pooled) incumbent - the winner's curse behind audit N5 / K3. Re-evaluations do not count toward
    ``eval_budget``; at most ``reeval_max_per_iteration`` per iteration.
    """

    iterations: int = 20                       # N
    k: int = 2                                 # candidates per iteration
    history_mode: str = "full"
    window: int = 5                            # window mode: last w evaluated candidates
    objectives: tuple[str, ...] = ("score", "context_cost")   # Pareto; ("score",) = scalar objective
    cost_metric: str = "context_chars"         # Execution.meta key averaged per unit; "tokens" = EvalResult.C
    search_split: str = "evolve"               # Meta-Harness "search set"
    test_splits: tuple[str, ...] = ("test",)   # evaluated ONCE, by finalize()
    trials: int = 1                            # seeds per unit
    reeval_incumbent: int = 0                  # extra seeds for every new _best (0 = off, faithful)
    reeval_max_per_iteration: int = 3
    tradeoff: str = ""                         # desired accuracy-context trade-off, stated to the proposer
    workers: int = 1                           # evaluator threads
    eval_budget: Optional[int] = None          # max candidate evaluations (equal-budget comparisons)
    leakage_screen: bool = False               # faithful default: no mechanical guard (pilot variant = True)
    validate: bool = True                      # interface validation before evaluation
    validate_timeout_s: float = 30.0           # "prints OK within 30 s"
    validate_in_subprocess: bool = True        # fork-isolated smoke (kills hung candidates)
    proposer_timeout_s: float = 2400.0         # claude_wrapper timeout
    finalize: bool = True                      # run finalize() at the end of run()
    summaries: str = "auto"                    # write trace summaries: auto (only scores_summary) | always | never
    seed: int = 0
    # audit trace (rsi.trace, write-only): on whenever out_dir is given to run()
    trace: bool = True
    shadow_monitor: bool = True                # score every new frontier _best on sealed holdout/ood (trace only)
    shadow_splits: Optional[tuple[str, ...]] = None   # None -> the domain's sealed holdout/ood splits
    shadow_k: int = 1
    shadow_workers: int = 2
    notes: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.history_mode not in HISTORY_MODES:
            raise ValueError(f"history_mode must be one of {HISTORY_MODES}")

    def to_json(self) -> dict:
        return asdict(self)
