"""AgentWorld: multi-step agent environments with large tool outputs (SoL-Pi's MiniAgentWorld).

Families ``repofix`` / ``buildfix`` / ``logtriage`` (training) and ``datalookup``
(held-out), a deterministic context-reading agent policy (:class:`MockAgent`,
profiles ``A`` and ``B`` = two backends) and an adapter for real LLM agents.

    from rsi.domains.agentworld import make_domain, MockAgentLLM
    dom = make_domain(seed=0)
    base = dom.seed_artifact()                                   # Pi-like base harness
    stack = dom.harness("action_fusion", "observation_pack",
                        "evidence_preserving_reducer", "online_context_compact")
    ev = Evaluator(dom, MockAgentLLM("A")).evaluate(stack, "evolve")
"""
from .domain import BASE_SYSTEM_PROMPT, HELDOUT_FAMILIES, TRAIN_FAMILIES, AgentWorldDomain, make_domain, render_trace
from .envs import (FAMILIES, BuildFixEnv, ConfigFixEnv, DataLookupEnv, Env, LogTriageEnv, RepoFixEnv, Subtask,
                   validity_filter)
from .policy import PROFILES, LLMAgent, MockAgent, MockAgentLLM, Profile, skill_for

__all__ = ["AgentWorldDomain", "make_domain", "render_trace", "BASE_SYSTEM_PROMPT", "TRAIN_FAMILIES",
           "HELDOUT_FAMILIES", "FAMILIES", "Env", "Subtask", "RepoFixEnv", "BuildFixEnv", "LogTriageEnv",
           "DataLookupEnv", "ConfigFixEnv", "validity_filter", "MockAgent", "MockAgentLLM", "LLMAgent", "Profile", "PROFILES", "skill_for"]
