"""Harness²: recursive agent harnessing for an open world (Xu et al., arXiv 2610.14224).

Per task, from the common harness H0 and with no grader, labels or score: k reference rollouts, scoped INV/DOM
editors (Propose), one rollout per candidate (Probe), composers that merge/adopt/keep from the behavioural contrast
(Compose), an anonymised deliverable-only selector at k > 1, then a reflector whose supported lesson enters a
per-domain library of adopted harnesses that later tasks read. See docs/methods/harness2/.
"""
from .api import StreamViolation, TaskStream, domain_of, eval_split, merged_usage, posthoc_report, run, stream_order
from .baselines import best_of_n
from .config import MODES, SLOTS, Config
from .guard import GraderAccessError, GraderLeak, OpenWorld, OpenWorldViolation, open_task
from .library import Library
from .loop import BudgetExceeded, TaskBudget, TaskRunner
from .mocks import MockComposer, MockEditor, MockImprover, MockReflector, MockSelector
from .records import Rollout, Stage, TaskRecord

__all__ = ["Config", "MODES", "SLOTS", "run", "posthoc_report", "best_of_n", "TaskStream", "StreamViolation",
           "domain_of", "eval_split", "stream_order", "merged_usage", "OpenWorld", "OpenWorldViolation",
           "GraderAccessError", "GraderLeak", "open_task", "Library", "TaskRunner", "TaskBudget", "BudgetExceeded",
           "MockImprover", "MockEditor", "MockComposer", "MockSelector", "MockReflector", "Rollout", "Stage",
           "TaskRecord"]
