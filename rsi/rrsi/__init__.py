"""RRSI: Regularized Recursive Self-Improvement of agent harnesses (Xia et al., 2026); entry point :func:`run`."""
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
