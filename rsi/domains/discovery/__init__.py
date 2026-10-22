"""CPU discovery domains (programs scored by a locked evaluator with typed ``fail_class``) for Dream-RSI and others."""
from .autocorr import AutocorrelationDomain
from .base import ProgramDomain, single_task_suite
from .circlepack import BEST_KNOWN as CIRCLE_BEST_KNOWN
from .circlepack import CirclePackingDomain
from .lasso import LassoPathDomain, make_instance, reference_objectives, solver_code
from .lasso_cpp import SimpleTESLassoDomain
from .sumdiff import SumDiffDomain, gamma
from .synthetic import SyntheticAgent, SyntheticConfig, SyntheticDomain, SyntheticWorld, phi

__all__ = ["ProgramDomain", "single_task_suite", "LassoPathDomain", "SimpleTESLassoDomain", "SumDiffDomain",
           "CirclePackingDomain", "AutocorrelationDomain", "CIRCLE_BEST_KNOWN", "SyntheticDomain", "SyntheticConfig",
           "SyntheticWorld", "SyntheticAgent", "phi", "gamma", "make_instance", "reference_objectives", "solver_code"]
