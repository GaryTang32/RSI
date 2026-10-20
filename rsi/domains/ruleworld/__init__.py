"""RuleWorld: a synthetic multi-module prompt-optimization domain with analytic ground truth (GEPA spec 9.2)."""
from .domain import FEEDBACK_LEVELS, RuleWorldDomain, make_domain
from .mocks import GROUNDED_MARKER, ReflectionProfile, RuleWorldReflectionLM, parse_side_info, split_fenced
from .world import ASPECTS, CODES, FAMILIES, STANDARD, Aspect, Example, ModuleView, RuleWorld, WorldConfig

__all__ = ["RuleWorldDomain", "make_domain", "FEEDBACK_LEVELS", "RuleWorldReflectionLM", "ReflectionProfile",
           "GROUNDED_MARKER", "parse_side_info", "split_fenced", "RuleWorld", "WorldConfig", "Aspect", "Example",
           "ModuleView", "ASPECTS", "CODES", "FAMILIES", "STANDARD"]
