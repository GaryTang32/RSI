"""SoL-Pi - auto-research loops for an efficient agent harness (Liu, Ye et al., NVIDIA, 2026).

Two layers:

1. **A Pi-like agent runtime and four efficiency mechanisms** (faithful ports of
   the released extensions): :class:`AgentRuntime` with a tool/hook extension API
   and a prefix-cache :class:`TokenMeter`; :class:`ActionFusion` (edit/write +
   ``then_run`` with a hash guard), :class:`ObservationPack` (placeholders after 2
   full sends, content-addressed handles, ``obs_recall``), :class:`EvidencePreservingReducer`
   (cheap-model receipts whose quotes are verified byte for byte, with fallback),
   :class:`OnlineContextCompact` (compact at plan boundaries only when the future
   saving repays the cache rewrite, :func:`decide_compaction`).
2. **The research protocol**: an :class:`IdeaPool` ranked by oracle analysis,
   disposable :class:`Lineage` loops (rollouts -> map-reduce analysis -> one
   mechanism -> Ralph-loop implementation -> review -> validation), a predeclared
   :class:`DualGate` (capability within tolerance AND an efficiency gain; aggregate
   or per-family), a one-way :class:`HoldoutFirewall`, nondominated survivors and
   :func:`compose`.

Quick start (any :class:`rsi.core.Domain` with task families + a held-out split)::

    from rsi.solpi import Config, GateSpec, run
    res = run(domain, base_harness, llm_task=agent_backend, llm_propose=None,   # None = offline proposer
              config=Config(gate=GateSpec(capability=(("score", 0.02),), efficiency=("tokens", "cost"),
                                          mode="aggregate")), out_dir="runs/solpi")
    res.best                                  # composed harness of the surviving mechanisms
    res.meta["rounds"][0]["survivor_ideas"]   # which ideas survived; ["heldout_passed"] firewall verdicts

Use the mechanisms directly in your own agent loop::

    rt = AgentRuntime(backend, system_prompt=..., meter=TokenMeter({"main": PRICES["sim-a"]}), env=my_env)
    for spec in builtin_tools(my_env): rt.register_tool(spec)
    for ext in (ActionFusion(), ObservationPack(), EvidencePreservingReducer(), OnlineContextCompact()):
        rt.add_extension(ext)
    rt.run(task_text); rt.meter.snapshot()

Module map: ``meter`` (TokenMeter, PriceTable, CostModel), ``runtime`` (AgentRuntime,
Message, ToolSpec, Extension, builtin tools, native compaction), ``fusion``,
``obspack``, ``reducer``, ``occ`` (the four mechanisms), ``tricks`` (negative
controls), ``registry`` (config -> extensions, code mechanisms, sol-pi.json),
``gate`` (GateSpec, DualGate, metrics, nondominated, HoldoutFirewall), ``research``
(Idea, IdeaPool, oracle, map-reduce, Lineage, Ralph loop, compose), ``mocks``
(idea pools, offline and LLM proposers/reviewers), ``driver`` (AutoResearchDriver,
Config, run).
"""
from .driver import AutoResearchDriver, Config, run
from .fusion import (EDIT_THEN_RUN_DESCRIPTION, THEN_RUN_FAILED, THEN_RUN_SKIPPED, THEN_RUN_SUCCEEDED, ActionFusion,
                     resolve_tool_path)
from .gate import DualGate, GateResult, GateSpec, HeldoutResult, HoldoutFirewall, Metrics, metrics_from_eval, \
    nondominated
from .meter import PRICES, CostModel, PriceTable, TokenMeter, estimate_tokens
from .mocks import (AGENTQA_IDEAS, AGENTWORLD_IDEAS, RUNTIME_API_DOC, AgentQAEditProposer, LibraryProposer,
                    LLMMechanismProposer, LLMReviewer)
from .obspack import FULL_SENDS, THRESHOLD_BYTES, Observation, ObservationPack, create_observation, placeholder_for
from .occ import (BOUNDARY_COMPACTION_INSTRUCTIONS, DEFAULT_COMPACTION_ECONOMICS, POST_COMPACTION_PLAN_REMINDER,
                  CompactionEconomics, OnlineContextCompact, OnlineState, decide_compaction,
                  estimate_remaining_requests)
from .reducer import (DIAGNOSTIC_COMMAND, FAILURE_SIGNAL, FALLBACK_REASONS, LIKELY_SECRET, ArchiveObject,
                      DeterministicReducer, EvidencePreservingReducer, LLMReducer, MockReducer, receipt_text,
                      reducer_input, reducer_instructions, validate_receipt)
from .registry import (DO_LESS, DUDS, GENERAL, MECHANISMS, ORDER, TRICKS, build_extensions, harness_config,
                       load_code_extension, parse_solpi_config, with_mechanism)
from .research import (FAMILY_CODES, FrozenCandidate, Idea, IdeaPool, Lineage, LineageResult, MechanismProposal,
                       SmokeReviewer, analyze, compose, implement, merge3, oracle_estimate, reduce_findings)
from .runtime import (EVENTS, AgentRuntime, Extension, Message, RunResult, ToolCall, ToolError, ToolResult,
                      ToolResultEvent, ToolSpec, builtin_tools, default_summarizer)
from .tricks import HeadTrim, NoVerify, PromptSlim, PytestQuiet, TailTrim, TurnCap

__all__ = [n for n in dir() if not n.startswith("_")]
