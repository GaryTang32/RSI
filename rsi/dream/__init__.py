"""Dream-RSI - improving the *search strategy* by replaying past searches (Zheng et al., 2026).

A finished discovery run is already a simulator: every attempt, the attempt it
started from and its score form a tree, and a different exploration policy can be
tested by walking that tree differently - revealing recorded outcomes instead of
running the agent. Dream-RSI keeps the models, the discovery agent and the evaluator
frozen and improves only the exploration policy, written as code: after every live
search the tree joins a pool of replay worlds, a policy developer rewrites the policy
M-1 times ("dreaming"), every version is replayed over all worlds, and the best one
(the incumbent included) runs the next live search.

Quick start (any :class:`rsi.core.Domain` + seed :class:`rsi.core.Artifact`, or a
discovery domain from :mod:`rsi.domains.discovery`)::

    from rsi.dream import Config, run
    from rsi.domains.discovery import CirclePackingDomain
    res = run(CirclePackingDomain(sandboxed=False), config=Config(rounds=5, W=4), out_dir="runs/cp")
    res.best                 # best discovered program
    res.meta["policy"]       # the final exploration-policy code (method.py)
    res.trajectory           # per live search: agent calls, best, plan, N, k, dreaming phase

Module map: ``policy_api`` (the policy-facing API, shipped into the sandbox),
``tree`` (DiscoveryNode / DiscoveryTree / ledger conversion / SnapshotStore),
``question`` (GridQuestion, ReplayQuestion, OnlineQuestion), ``guard`` (PrefixGuard,
static check, in-process and subprocess runners), ``policy`` + ``policy_templates/``
(policies as code), ``objectives`` (Eq. 1, Pareto beta sweep), ``evaluator``
(ReplayEvaluator, PolicyReport), ``developer`` (LLM developer with the Listing-2 prompt,
ParametricMutator, default-beta rule), ``selection`` (argmax incl. incumbent,
GuardedSelector), ``agent`` (EvalOutcome, DiscoveryTask, DomainTask, EditorAgent with
the Listing-1 prompt, ParametricAgent, DirectionProvider), ``baselines`` (fixed
exploration, guidance summarizers), ``cost`` (CostMeter), ``loop`` (Config,
DreamRSILoop, run), ``demo`` (the overview's interactive replay demo).
"""
from .agent import (AgentAttempt, AttemptContext, AttemptRecord, DirectionProvider, DiscoveryAgent, DiscoveryTask,
                    DomainTask, EditorAgent, EvalOutcome, ParametricAgent)
from .baselines import (Guidance, GuidanceSummarizer, LLMGuidanceSummarizer, MockGuidanceSummarizer, direction_gains,
                        fixed_config)
from .cost import CostMeter
from .demo import DEMO_EXPECTED, demo_policies, demo_tree, render_demo, run_demo
from .developer import (DEVELOPER_PROMPT, DevContext, LLMPolicyDeveloper, ParametricMutator, PolicyDeveloper,
                        Revision, VersionRecord, choose_default_beta, mock_developer_llm)
from .evaluator import PolicyReport, ReplayEvaluator, diagnostics
from .guard import (CheckResult, InProcessRunner, PrefixGuard, SolveOutcome, SubprocessRunner, get_runner,
                    static_check)
from .loop import Config, DreamRSILoop, as_task, live_manifest, run
from .objectives import EpisodeResult, Eq1Objective, ParetoSweepObjective
from .policy import (POLICY_FILE, adaptive, code_of, get_params, make_policy, parallel_refine, policy_artifact, rules,
                     set_params, template_code)
from .policy_api import (BatchError, CellMeta, GridPlan, GridPlanningContext, GuardViolation, LLMDesignedMethod,
                         Observation, QuestionProxy, SimResult)
from .question import GridQuestion, OnlineQuestion, ReplayQuestion, program_only, workspace_of
from .selection import GuardedSelector, SelectionResult, Selector
from .tree import ROOT_ID, DiscoveryNode, DiscoveryTree, SnapshotStore, cell_id, parse_cell

__all__ = [
    "Config", "run", "DreamRSILoop", "as_task", "live_manifest",
    "DiscoveryNode", "DiscoveryTree", "SnapshotStore", "ROOT_ID", "cell_id", "parse_cell",
    "GridQuestion", "ReplayQuestion", "OnlineQuestion", "workspace_of", "program_only",
    "Observation", "CellMeta", "GridPlan", "GridPlanningContext", "LLMDesignedMethod", "SimResult", "QuestionProxy",
    "GuardViolation", "BatchError",
    "PrefixGuard", "static_check", "CheckResult", "InProcessRunner", "SubprocessRunner", "get_runner", "SolveOutcome",
    "POLICY_FILE", "template_code", "policy_artifact", "code_of", "get_params", "set_params", "make_policy",
    "parallel_refine", "adaptive", "rules",
    "EpisodeResult", "Eq1Objective", "ParetoSweepObjective", "ReplayEvaluator", "PolicyReport", "diagnostics",
    "PolicyDeveloper", "LLMPolicyDeveloper", "ParametricMutator", "DevContext", "VersionRecord", "Revision",
    "choose_default_beta", "mock_developer_llm", "DEVELOPER_PROMPT",
    "Selector", "GuardedSelector", "SelectionResult",
    "EvalOutcome", "DiscoveryTask", "DomainTask", "DiscoveryAgent", "EditorAgent", "ParametricAgent",
    "AttemptContext", "AttemptRecord", "AgentAttempt", "DirectionProvider",
    "Guidance", "GuidanceSummarizer", "MockGuidanceSummarizer", "LLMGuidanceSummarizer", "direction_gains",
    "fixed_config", "CostMeter",
    "run_demo", "render_demo", "demo_tree", "demo_policies", "DEMO_EXPECTED",
]
