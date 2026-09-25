"""RRSI - Regularized Recursive Self-Improvement of agent harnesses (Xia et al., 2026).

A harness-evolution loop that regularizes *how the search moves*, not what may be
edited. Proposal side: an annealed L0 edit budget b_t, an evidence-aware edit history
L_t (falsified ideas stay falsified), stall-triggered exploration of never-exercised
components. Selection side: a regex + LLM leakage critic before any evaluation, a
noise-adjusted floor S' >= S* - delta, a cost rule dC <= beta0 + beta1*dS (and a shaped
rule inside the noise band), domain guards, and pruning directives for components
that stopped paying for themselves.

Quick start (any :class:`rsi.core.Domain` + seed :class:`rsi.core.Artifact`)::

    from rsi.rrsi import Config, run
    res = run(domain, seed_artifact, llm_task=task_llm, llm_propose=proposer_llm,
              config=Config(T=10), out_dir="runs/my_problem")
    res.best            # the final incumbent harness H_T
    res.trajectory      # one row per round (S, C, S*, b_t, sigma_t, decisions)

Module map (mirrors google-research/rrsi): ``config`` (hyperparameters + presets),
``switches`` (ablations), ``schedule`` (b_t), ``components`` (K, K_str, diff tagging,
novelty), ``history`` (L_t, T_t, g_t, U_t, sigma_t, B_t), ``evaluate`` (S_hat, C_hat,
missing = 0), ``calibrate`` (delta), ``analyst`` (F_t), ``propose`` (done() contract),
``critic`` (leakage screen), ``selection`` (Algorithm 2 on rsi.core gates),
``attribution`` (scoreboard), ``frontier``, ``loop`` (rounds, readjudicate, resume),
``driver`` (STOP file, infra limit, :func:`run`), ``transfer`` (paired H_0 vs final),
``toy`` (the overview's overfitting-trap simulator), ``tracing`` (per-iteration trace +
write-only shadow monitor), ``audit`` (re-derives every traced step with the paper's formulas).
"""
from .analyst import Analyst, build_traces
from .attribution import Scoreboard
from .calibrate import calibrate
from .components import K, K_STR, Taxonomy
from .config import PRESETS, Config
from .constitution import default_constitution
from .critic import RRSICritic
from .driver import Killed, drive, merged_usage, result, run
from .evaluate import Measurement, Measurer, aggregate
from .history import History, exploration, stall_flag
from .loop import RRSIRun
from .propose import Proposer, RRSIRewriteEditor, parse_done, parse_sections
from .schedule import budget_table, edit_budget
from .selection import Candidate, Decision, build_gates, cost_rule, judge, select_round
from .switches import ARMS, RegularizerSwitches
from .transfer import paired_transfer

__all__ = [
    "Config", "PRESETS", "RegularizerSwitches", "ARMS", "run", "drive", "result", "RRSIRun", "Killed",
    "edit_budget", "budget_table", "K", "K_STR", "Taxonomy", "History", "stall_flag", "exploration",
    "Measurement", "Measurer", "aggregate", "calibrate", "Analyst", "build_traces", "Proposer",
    "RRSIRewriteEditor", "parse_done", "parse_sections", "RRSICritic", "Candidate", "Decision", "build_gates",
    "cost_rule", "judge", "select_round", "Scoreboard", "paired_transfer", "merged_usage", "default_constitution",
]
