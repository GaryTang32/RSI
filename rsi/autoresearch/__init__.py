"""autoresearch: an agent edits one training script; the framework keeps the commit iff the metric strictly improves."""
from .agent import (AgentContext, LLMResearchAgent, MockResearchAgent, RandomSearchAgent, ResearchAgent, ScriptedEdit,
                    knob_edit, scripted_llm, text_edit)
from .analysis import Analyzer, HiddenAudit, Reeval
from .guard import BudgetEnforcer, CrashPolicy, ScopeGuard, Violation, default_tamper_patterns
from .keep import (BootstrapRigorKeep, GateKeep, KeepContext, KeepRule, NoiseCalibrator, Samples, SimplicityWeighted,
                   StrictKeep, UpstreamKeep, make_keep_rule)
from .landscape import LandscapeTask, landscape_edit_pool
from .loop import AutoresearchLoop, Config, agent_llms, make_agent, run
from .program import ProgramSpec
from .results import ResultsLog, Row, Workspace
from .task import DomainResearchTask, ResearchTask, RunBudget, RunOutcome, ScriptResearchTask, parse_summary

__all__ = [
    "Config", "run", "AutoresearchLoop", "make_agent", "agent_llms",
    "ResearchTask", "ScriptResearchTask", "DomainResearchTask", "RunBudget", "RunOutcome", "parse_summary",
    "ScopeGuard", "Violation", "default_tamper_patterns", "BudgetEnforcer", "CrashPolicy",
    "ResultsLog", "Row", "Workspace",
    "KeepRule", "UpstreamKeep", "StrictKeep", "BootstrapRigorKeep", "SimplicityWeighted", "GateKeep",
    "NoiseCalibrator", "Samples", "KeepContext", "make_keep_rule",
    "ProgramSpec",
    "ResearchAgent", "LLMResearchAgent", "MockResearchAgent", "RandomSearchAgent", "AgentContext", "ScriptedEdit",
    "knob_edit", "text_edit", "scripted_llm",
    "Analyzer", "HiddenAudit", "Reeval",
    "LandscapeTask", "landscape_edit_pool",
]
