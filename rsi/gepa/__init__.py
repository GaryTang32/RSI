"""GEPA - Genetic-Pareto reflective prompt evolution (Agrawal et al., ICLR 2026).

Candidates are :class:`rsi.core.Artifact` objects whose evolvable *components* are
named text files (module prompts, skills, ...). Each iteration picks a parent from
the per-instance Pareto frontier over D_pareto, runs it on an epoch-shuffled
minibatch of b = 3 D_train examples with traces, shows a reflection LM the
``{Inputs, Generated Outputs, Feedback}`` records through GEPA's verbatim
meta-prompt, rewrites one component (round-robin), keeps the child only if the
minibatch sum strictly improves, then scores it on all of D_pareto and adds it to a
lineage tree. GEPA+Merge adds system-aware crossover of module texts from two
lineages that share an ancestor. The result is the candidate with the best mean
D_pareto score; sealed splits are scored only by :func:`report`.

Quick start (any :class:`rsi.core.Domain` + seed :class:`rsi.core.Artifact`)::

    from rsi.gepa import Config, run
    res = run(domain, seed_artifact, llm_task=task_llm, llm_propose=reflection_llm,
              config=Config(max_metric_calls=800), out_dir="runs/my_problem",
              report_splits=("test",))
    res.best                 # argmax mean D_pareto score
    res.meta["rollouts_by_phase"], res.meta["report"]
    res.state                # lineage, per-instance scores, frontier, iteration trace

Or without writing a Domain: :func:`optimize` ``(seed_dict, trainset, valset,
metric=..., llm_propose=...)``.

Module map: ``config`` (hyperparameters), ``adapter`` (Domain -> EvalBatch +
reflective dataset), ``reflection`` (meta-prompts, rendering, fenced parsing,
ReflectionProposer), ``frontier`` (Pareto frontier + Algorithm 2), ``strategies``
(selectors, round-robin, epoch-shuffled sampler, acceptance), ``merge`` (Algorithms
3-4), ``state`` (SearchState, rollout counter, resume), ``stoppers``, ``engine`` (the
loop), ``api`` (run / optimize / report), ``baselines`` (ScalarRL, ScoreOnly,
FewShotDemo, BestOfN), ``analysis`` (tree metrics, curves), ``mocks`` (offline
reflection LMs + a two-module AgentQA harness).
"""
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
from .stoppers import (BudgetStopper, Composite, ConsecutiveInfraFailures, FileStopper, MaxCandidateProposals, MaxIterations, MaxMetricCalls,
                       MaxReflectionCost, NoImprovement, ScoreThreshold, Timeout)
from .strategies import (AllComponents, BeamSearchSelector, CurrentBestSelector, EpochShuffledBatchSampler,
                         EpsilonGreedySelector, ImprovementOrEqual, NoiseMargin, ParetoSelector, RoundRobinComponents,
                         StrictImprovement, TopKParetoSelector, make_selector)

__all__ = [
    "Config", "run", "optimize", "report", "build_result", "default_components", "MetricDomain",
    "GEPAEngine", "BudgetExhausted", "merge_usage", "DomainAdapter", "EvalBatch", "GEPAAdapter",
    "FrontierTracker", "remove_dominated_programs", "find_dominator_programs", "pareto_frequencies",
    "select_from_pareto_front", "MergeProposer", "merge_texts", "sample_and_attempt_merge", "select_eval_subsample",
    "DEFAULT_TEMPLATE", "OPTIMIZE_ANYTHING_TEMPLATE", "ReflectionProposer", "build_prompt", "parse_fenced",
    "render_samples", "PHASES", "RolloutCounter", "SearchState", "MaxMetricCalls", "MaxIterations", "Timeout",
    "FileStopper", "ScoreThreshold", "NoImprovement", "MaxCandidateProposals", "MaxReflectionCost", "BudgetStopper",
    "Composite", "ConsecutiveInfraFailures", "ParetoSelector", "CurrentBestSelector", "EpsilonGreedySelector", "TopKParetoSelector",
    "BeamSearchSelector", "make_selector", "RoundRobinComponents", "AllComponents", "EpochShuffledBatchSampler",
    "StrictImprovement", "ImprovementOrEqual", "NoiseMargin",
    "RLConfig", "ScalarRLBaseline", "run_scalar_rl", "BRAINSTORM_TEMPLATE", "run_score_only", "run_best_of_n",
    "FewShotConfig", "FewShotDemoOptimizer", "run_fewshot", "GROUNDED_TEMPLATE", "tree_metrics", "gepa_curve",
    "trajectory_curve", "curve_at", "rollouts_to_target", "gate_errors", "GenericReflectionLM",
    "AgentQAReflectionLM", "two_module_harness",
]
