"""HarnessWorld: a synthetic harness-evolution domain with analytic ground truth, plus offline mock LLMs."""

from .domain import HarnessWorldDomain, make_domain
from .mocks import DEFAULT_SHARES, CriticProfile, HarnessWorldMockLLM, ProposerProfile
from .world import KINDS, STRONG, STRUCTURAL, WEAK, K, Mechanism, Policy, World, WorldConfig

__all__ = [
    "HarnessWorldDomain",
    "make_domain",
    "HarnessWorldMockLLM",
    "ProposerProfile",
    "CriticProfile",
    "DEFAULT_SHARES",
    "World",
    "WorldConfig",
    "Mechanism",
    "Policy",
    "STRONG",
    "WEAK",
    "K",
    "KINDS",
    "STRUCTURAL",
]
