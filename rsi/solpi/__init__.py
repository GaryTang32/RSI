"""SoL-Pi (Liu, Ye et al., NVIDIA, 2026): a Pi-like agent runtime, four efficiency mechanisms, research protocol."""
from .driver import AutoResearchDriver, Config, run
from .fusion import (EDIT_THEN_RUN_DESCRIPTION, THEN_RUN_FAILED, THEN_RUN_SKIPPED, THEN_RUN_SUCCEEDED,
                     WRITE_THEN_RUN_DESCRIPTION, ActionFusion, canonical_queue_key, resolve_tool_path)
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
from .registry import (DO_LESS, DUDS, EVALUATION, GENERAL, MECHANISMS, ORDER, TRICKS, build_extensions,
                       harness_config, load_code_extension, parse_solpi_config, with_mechanism)
from .research import (FAMILY_CODES, FrozenCandidate, Idea, IdeaPool, Lineage, LineageResult, MechanismProposal,
                       SmokeReviewer, analyze, compose, implement, merge3, oracle_estimate, reduce_findings)
from .runtime import (EVENTS, AgentRuntime, Extension, Message, RunResult, ToolCall, ToolError, ToolResult,
                      ToolResultEvent, ToolSpec, builtin_tools, default_summarizer)
from .tricks import CostAttribution, FailBeforePassAfter, HeadTrim, NoVerify, PromptSlim, PytestQuiet, TailTrim, TurnCap

__all__ = [n for n in dir() if not n.startswith("_")]
