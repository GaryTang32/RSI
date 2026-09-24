"""rsi - a transferable toolkit for recursive self-improvement loops.

Every technique here runs the same loop - propose a change, run it, score it,
keep or discard - and differs in *what* changes, *who* checks, and *how far* an
improvement spreads:

* ``rsi.autoresearch`` - improve the work (a training script), strict keep rule.
* ``rsi.rrsi``         - improve the agent harness, with seven regularizing guards.
* ``rsi.dream``        - improve the search strategy by replaying past searches.
* ``rsi.evomap``       - share improvements across a population as small "genes".
* ``rsi.gepa``         - reflective prompt evolution with a Pareto frontier.
* ``rsi.metaharness``  - a coding agent that reads the full history of attempts.
* ``rsi.solpi``        - efficiency mechanisms that survive across environments.

``rsi.core`` holds the shared machinery (LLM backends, artifacts, task splits,
evaluation, statistics, keep rules, ledgers, sandbox).
"""
from .core import *  # noqa: F401,F403

__version__ = "0.1.0"
