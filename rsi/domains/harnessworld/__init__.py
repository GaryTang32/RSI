"""HarnessWorld: a synthetic harness-evolution domain with analytic ground truth
(RRSI spec section 9.2, Tier 1).

A harness is a set of mechanism files; each catalog mechanism has a true generic effect
per task family, a suite-specific trick effect that only helps listed evolve tasks
(while its leak payload is present), a token cost and possibly decaying usefulness.
Evaluation is noisy (Binomial rubric criteria, lognormal tokens); ground truth E[S],
E[C] is analytic. :class:`HarnessWorldMockLLM` is a parametric proposer + leakage critic
for offline runs.

    from rsi.domains.harnessworld import make_domain, HarnessWorldMockLLM
    dom = make_domain(seed=0)
    llm = HarnessWorldMockLLM(dom.world)
"""
from .domain import HarnessWorldDomain, make_domain
from .mocks import DEFAULT_SHARES, CriticProfile, HarnessWorldMockLLM, ProposerProfile
from .world import K, KINDS, STRONG, STRUCTURAL, WEAK, Mechanism, Policy, World, WorldConfig

__all__ = ["HarnessWorldDomain", "make_domain", "HarnessWorldMockLLM", "ProposerProfile", "CriticProfile",
           "DEFAULT_SHARES", "World", "WorldConfig", "Mechanism", "Policy", "STRONG", "WEAK", "K", "KINDS",
           "STRUCTURAL"]
