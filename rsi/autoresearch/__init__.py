"""autoresearch: Karpathy's overnight research loop (March 2026) and its community ports.

A coding agent edits one training script, the framework trains it for a fixed
budget, reads one number, keeps the commit if the number is strictly better and
resets otherwise - forever, logging every run to ``results.tsv``.

Quick start (offline, scripted agent)::

    from rsi.autoresearch import Config, run
    from rsi.domains.tinylm import TinyLMTask

    res = run(TinyLMTask(budget_s=5), config=Config(max_experiments=20), out_dir="runs/tinylm")
    print(res.meta["analysis"])            # keep rate, running best, top hits
    # runs/tinylm/results.tsv, progress.png, groundtruth_all.tsv, ledger.jsonl

Any :class:`rsi.core.Domain` works too (``run(domain, seed_artifact, llm_task=..., llm_propose=...)``).

Modules: task (ResearchTask, ScriptResearchTask, DomainResearchTask, RunBudget),
guard (ScopeGuard with the hardened tamper scan, BudgetEnforcer, CrashPolicy), results (ResultsLog, Workspace),
keep (StrictKeep, BootstrapRigorKeep, SimplicityWeighted, GateKeep, NoiseCalibrator),
program (ProgramSpec), agent (LLMResearchAgent, MockResearchAgent), loop
(AutoresearchLoop, Config, run), analysis (Analyzer, HiddenAudit, Reeval),
executors (LocalProcessExecutor, FakeSlurmExecutor), parallel
(ParallelAutoresearchLoop, MultiChainLauncher), hub (SharedHub, CollaborativeLoop),
landscape (LandscapeTask, a known-truth synthetic task).
"""
from .agent import (AgentContext, LLMResearchAgent, MockResearchAgent, RandomSearchAgent, ResearchAgent, ScriptedEdit,
                    knob_edit, scripted_llm, text_edit)
from .analysis import Analyzer, HiddenAudit, Reeval
from .guard import BudgetEnforcer, CrashPolicy, ScopeGuard, Violation, default_tamper_patterns
from .keep import (BootstrapRigorKeep, GateKeep, KeepContext, KeepRule, NoiseCalibrator, Samples, SimplicityWeighted,
                   StrictKeep, make_keep_rule)
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
    "KeepRule", "StrictKeep", "BootstrapRigorKeep", "SimplicityWeighted", "GateKeep", "NoiseCalibrator", "Samples",
    "KeepContext", "make_keep_rule",
    "ProgramSpec",
    "ResearchAgent", "LLMResearchAgent", "MockResearchAgent", "RandomSearchAgent", "AgentContext", "ScriptedEdit", "knob_edit",
    "text_edit", "scripted_llm",
    "Analyzer", "HiddenAudit", "Reeval",
    "LandscapeTask", "landscape_edit_pool",
]
