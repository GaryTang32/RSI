"""Dream-RSI: improve the exploration policy of a discovery loop by replaying past searches (Zheng et al., 2026)."""
from .agent import (EXPLORATION_PROMPT, AgentAttempt, AttemptContext, AttemptRecord, DirectionProvider, DiscoveryAgent,
                    DiscoveryTask, DomainTask, EditorAgent, EvalOutcome, ParametricAgent)
from .baselines import (Guidance, GuidanceSummarizer, LLMGuidanceSummarizer, MockGuidanceSummarizer, direction_gains,
                        fixed_config)
from .cost import CostMeter
from .demo import DEMO_EXPECTED, demo_policies, demo_tree, render_demo, run_demo
from .developer import (DEVELOPER_PROMPT, FRAMEWORK_NOTES, DevContext, LLMPolicyDeveloper, ParametricMutator,
                        PolicyDeveloper, Revision, VersionRecord, choose_default_beta, developer_prompt,
                        mock_developer_llm)
from .evaluator import PolicyReport, ReplayEvaluator, diagnostics
from .guard import (CheckResult, InProcessRunner, PrefixGuard, SolveOutcome, SubprocessRunner, get_runner,
                    static_check)
from .loop import Config, DreamRSILoop, as_task, live_manifest, run
from .objectives import EpisodeResult, Eq1Objective, HypervolumeObjective, ParetoSweepObjective
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
    "EpisodeResult", "Eq1Objective", "HypervolumeObjective", "ParetoSweepObjective", "ReplayEvaluator", "PolicyReport",
    "diagnostics",
    "PolicyDeveloper", "LLMPolicyDeveloper", "ParametricMutator", "DevContext", "VersionRecord", "Revision",
    "choose_default_beta", "mock_developer_llm", "DEVELOPER_PROMPT", "FRAMEWORK_NOTES", "developer_prompt",
    "EXPLORATION_PROMPT",
    "Selector", "GuardedSelector", "SelectionResult",
    "EvalOutcome", "DiscoveryTask", "DomainTask", "DiscoveryAgent", "EditorAgent", "ParametricAgent",
    "AttemptContext", "AttemptRecord", "AgentAttempt", "DirectionProvider",
    "Guidance", "GuidanceSummarizer", "MockGuidanceSummarizer", "LLMGuidanceSummarizer", "direction_gains",
    "fixed_config", "CostMeter",
    "run_demo", "render_demo", "demo_tree", "demo_policies", "DEMO_EXPECTED",
]
