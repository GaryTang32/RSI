"""Meta-Harness - end-to-end harness optimisation from the full history (Lee et al., 2026).

A proposer (a coding agent in the paper) reads a filesystem holding *every*
earlier candidate's source code, scores and raw execution traces, and writes k
new whole-program harnesses per iteration. An evaluator outside the proposer
scores each valid candidate on the search split and stores everything back.
There is no parent-selection rule and no keep gate: the output is the Pareto
frontier (score up, context cost down) plus per-unit bests, and the test split is
evaluated exactly once at finalisation, after which evolution is locked.

Quick start (any :class:`rsi.core.Domain` + seed :class:`rsi.core.Artifact`)::

    from rsi.metaharness import Config, run
    res = run(domain, seed_artifact, llm_task=frozen_model, llm_propose=ClaudeCLI("sonnet"),
              config=Config(iterations=10, k=2), out_dir="runs/mh", baselines={"a": art_a, "b": art_b})
    res.best                          # highest-score Pareto point (search split)
    res.meta["frontier"]["_pareto"]   # the frontier
    res.meta["final"]                 # one-time test evaluation of frontier + baselines

Offline: ``llm_propose=None`` uses :class:`MockProposer` (program libraries exist
for ``memoclassify`` and ``agentqa``). History-access ablations: ``Config(history_mode=
"scores_only" | "scores_summary" | "full")``; equal-budget baseline arms: ``"seed_only"``
(Best-of-N), ``"window"`` (OPRO-style), ``"last_only"`` (GEPA/TextGrad-style reflection).

Module map: ``config`` (Config, history modes), ``store`` (ExperienceStore = the
filesystem D, views, sealed results, history CLI helpers), ``frontier`` (Pareto,
per-unit best, hypervolume), ``proposer`` (skill/task prompts, AgentProposer,
RewriteProposer, summarisers), ``mock`` (MockProposer + program libraries),
``validate`` (InterfaceValidator, LeakageScreen), ``loop`` (MetaHarnessLoop),
``api`` (run, make_proposer).
"""
from .api import make_proposer, run
from .config import HISTORY_MODES, Config
from .frontier import dominates, hypervolume, pareto_frontier, per_unit_best
from .loop import MetaHarnessLoop
from .mock import AgentQALibrary, MemoClassifyLibrary, MockProposer, library_for
from .proposer import (AgentProposer, CandidateSpec, LLMSummarizer, ProposalBatch, Proposer, RewriteProposer,
                       SKILL_TEXT, TASK_PROMPT, render_view)
from .store import ExperienceStore, FinalizedError, cli_diff, cli_frontier, cli_show, cli_top, safe_name
from .validate import InterfaceValidator, LeakageScreen

__all__ = ["Config", "run", "make_proposer", "HISTORY_MODES", "MetaHarnessLoop", "ExperienceStore",
           "FinalizedError", "pareto_frontier", "per_unit_best", "hypervolume", "dominates", "Proposer",
           "ProposalBatch", "CandidateSpec", "AgentProposer", "RewriteProposer", "LLMSummarizer", "SKILL_TEXT",
           "TASK_PROMPT", "render_view", "MockProposer", "MemoClassifyLibrary", "AgentQALibrary", "library_for",
           "InterfaceValidator", "LeakageScreen", "cli_frontier", "cli_top", "cli_diff", "cli_show", "safe_name"]
