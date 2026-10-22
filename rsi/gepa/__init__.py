"""GEPA: Genetic-Pareto reflective prompt evolution (Agrawal et al., ICLR 2026)."""
from .adapter import DomainAdapter, EvalBatch, GEPAAdapter
from .analysis import curve_at, gate_errors, gepa_curve, rollouts_to_target, trajectory_curve, tree_metrics
from .baselines import (FewShotConfig, FewShotDemoOptimizer, GROUNDED_TEMPLATE, run_best_of_n, run_fewshot,
                        run_score_only)
from .api import MetricDomain, build_result, default_components, optimize, report, run
from .config import Config
from .engine import BudgetExhausted, GEPAEngine, merge_usage
from .frontier import (FrontierTracker, find_dominator_programs, pareto_frequencies, remove_dominated_programs,
                       select_from_pareto_front)
from .mocks import AgentQAReflectionLM, GenericReflectionLM, two_module_harness
from .merge import MergeProposer, merge_texts, sample_and_attempt_merge, select_eval_subsample
from .reflection import (DEFAULT_TEMPLATE, OPTIMIZE_ANYTHING_TEMPLATE, ReflectionProposer, build_prompt, parse_fenced,
                         render_samples)
from .rl import BRAINSTORM_TEMPLATE, RLConfig, ScalarRLBaseline, run_scalar_rl
from .state import PHASES, RolloutCounter, SearchState
from .stoppers import (BudgetStopper, Composite, ConsecutiveInfraFailures, FileStopper, MaxCandidateProposals,
                       MaxIterations, MaxMetricCalls, MaxReflectionCost, NoImprovement, ScoreThreshold, Timeout)
from .strategies import (AllComponents, BeamSearchSelector, CurrentBestSelector, EpochShuffledBatchSampler,
                         EpsilonGreedySelector, ImprovementOrEqual, NoiseMargin, ParetoSelector, RoundRobinComponents,
                         StrictImprovement, TopKParetoSelector, make_selector)

__all__ = [
    "Config", "run", "optimize", "report", "build_result", "default_components", "MetricDomain", "GEPAEngine",
    "BudgetExhausted", "merge_usage", "DomainAdapter", "EvalBatch", "GEPAAdapter", "FrontierTracker",
    "remove_dominated_programs", "find_dominator_programs", "pareto_frequencies", "select_from_pareto_front",
    "MergeProposer", "merge_texts", "sample_and_attempt_merge", "select_eval_subsample", "DEFAULT_TEMPLATE",
    "OPTIMIZE_ANYTHING_TEMPLATE", "ReflectionProposer", "build_prompt", "parse_fenced", "render_samples", "PHASES",
    "RolloutCounter", "SearchState", "MaxMetricCalls", "MaxIterations", "Timeout", "FileStopper", "ScoreThreshold",
    "NoImprovement", "MaxCandidateProposals", "MaxReflectionCost", "BudgetStopper", "Composite",
    "ConsecutiveInfraFailures", "ParetoSelector", "CurrentBestSelector", "EpsilonGreedySelector",
    "TopKParetoSelector", "BeamSearchSelector", "make_selector", "RoundRobinComponents", "AllComponents",
    "EpochShuffledBatchSampler", "StrictImprovement", "ImprovementOrEqual", "NoiseMargin", "RLConfig",
    "ScalarRLBaseline", "run_scalar_rl", "BRAINSTORM_TEMPLATE", "run_score_only", "run_best_of_n", "FewShotConfig",
    "FewShotDemoOptimizer", "run_fewshot", "GROUNDED_TEMPLATE", "tree_metrics", "gepa_curve", "trajectory_curve",
    "curve_at", "rollouts_to_target", "gate_errors", "GenericReflectionLM", "AgentQAReflectionLM",
    "two_module_harness",
]
