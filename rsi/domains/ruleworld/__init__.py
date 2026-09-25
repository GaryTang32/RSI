"""RuleWorld: a synthetic multi-module prompt-optimization domain with analytic ground truth
(GEPA spec section 9.2, Tier 1).

The task model's accuracy depends on which general rules its module prompts contain;
some rules conflict across customer families (per-instance trade-offs) unless
conditioned; ticket-specific facts only help the ticket they name (overfitting
lure); feedback comes at three levels (rich / symptom / score_only).

    from rsi.domains.ruleworld import make_domain, RuleWorldReflectionLM
    dom = make_domain(seed=0, feedback="rich")
    refl = RuleWorldReflectionLM(dom.world)          # offline reflection LM
    dom.expected(dom.oracle_artifact(), "test")      # exact ground truth
"""
from .domain import FEEDBACK_LEVELS, RuleWorldDomain, make_domain
from .mocks import GROUNDED_MARKER, ReflectionProfile, RuleWorldReflectionLM, parse_side_info, split_fenced
from .world import ASPECTS, CODES, FAMILIES, STANDARD, Aspect, Example, ModuleView, RuleWorld, WorldConfig

__all__ = ["RuleWorldDomain", "make_domain", "FEEDBACK_LEVELS", "RuleWorldReflectionLM", "ReflectionProfile",
           "GROUNDED_MARKER", "parse_side_info", "split_fenced", "RuleWorld", "WorldConfig", "Aspect", "Example",
           "ModuleView", "ASPECTS", "CODES", "FAMILIES", "STANDARD"]
