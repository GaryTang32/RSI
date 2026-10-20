"""Meta-Harness: end-to-end harness optimisation from the full history of candidates (Lee et al., 2026)."""
from .api import make_proposer, run
from .baselines import POLICIES, StructuredOptimizerProposer
from .config import HISTORY_MODES, Config
from .frontier import dominates, hypervolume, pareto_frontier, per_unit_best
from .loop import MetaHarnessLoop
from .mock import AgentQALibrary, MemoClassifyLibrary, MockProposer, library_for
from .proposer import (READ_MIX, AgentProposer, CandidateSpec, LLMSummarizer, ProposalBatch, Proposer,
                       RewriteProposer, SKILL_TEXT, TASK_PROMPT, TranscriptCLI, render_view)
from .store import ExperienceStore, FinalizedError, cli_diff, cli_frontier, cli_show, cli_top, safe_name
from .validate import InterfaceValidator, LeakageScreen

__all__ = ["Config", "run", "make_proposer", "HISTORY_MODES", "MetaHarnessLoop", "ExperienceStore",
           "FinalizedError", "pareto_frontier", "per_unit_best", "hypervolume", "dominates", "Proposer",
           "ProposalBatch", "CandidateSpec", "AgentProposer", "RewriteProposer", "LLMSummarizer", "SKILL_TEXT",
           "TASK_PROMPT", "render_view", "READ_MIX", "TranscriptCLI", "MockProposer", "MemoClassifyLibrary",
           "AgentQALibrary", "library_for", "InterfaceValidator", "LeakageScreen", "StructuredOptimizerProposer",
           "POLICIES", "cli_frontier", "cli_top", "cli_diff", "cli_show", "safe_name"]
