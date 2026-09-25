"""CPU discovery domains for Dream-RSI (and any other method in ``rsi``).

Each node of a discovery search is a *program* written by a discovery agent (an LLM
editor, or a deterministic mock agent) and scored by a locked evaluator:

* :class:`LassoPathDomain` - Lasso regularization-path solver runtime, correctness gate
  ``F_k <= F_k(reference) + 1e-6`` on fresh instances, held-out instances for transfer;
* :class:`SumDiffDomain` - sum-difference sets, maximize log(|A+A|/|A|)/log(|A-A|/|A|);
* :class:`CirclePackingDomain` - 26 circles in the unit square, maximize the sum of radii;
* :class:`SyntheticDomain` - a synthetic discovery-world generator with ground-truth
  branch quality, for off-policy validation of replay.

All are :class:`rsi.core.Domain` subclasses with ``seed_artifact()``, ``evaluate_program``
(the locked grader returning typed ``fail_class`` outcomes), ``directions()`` (mechanism
classes for the direction provider), ``mock_agent()`` and ``as_task()``.
"""
from .base import ProgramDomain, single_task_suite
from .circlepack import BEST_KNOWN as CIRCLE_BEST_KNOWN
from .circlepack import CirclePackingDomain
from .lasso import LassoPathDomain, make_instance, reference_objectives, solver_code
from .sumdiff import SumDiffDomain, gamma
from .synthetic import SyntheticAgent, SyntheticConfig, SyntheticDomain, SyntheticWorld, phi

__all__ = ["ProgramDomain", "single_task_suite", "LassoPathDomain", "SumDiffDomain", "CirclePackingDomain",
           "CIRCLE_BEST_KNOWN", "SyntheticDomain", "SyntheticConfig", "SyntheticWorld", "SyntheticAgent", "phi",
           "gamma", "make_instance", "reference_objectives", "solver_code"]
