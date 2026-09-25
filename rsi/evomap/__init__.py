"""Gene hub: shared, inheritable strategy genes (EvoMap / Evolver / GEP, reproduced under a neutral name).

EvoMap's idea: agents should not each learn the same lesson alone. When an agent
solves a problem, it records what worked as small content-hashed assets - a
**Gene** (compact strategy card), a **Capsule** (worked case trace) and an
**EvolutionEvent** (append-only audit record) - and can publish them to a hub
where other agents, possibly on other models, inherit them. This package
implements the local engine faithfully (signals, memory graph, selection with
drift, solidify with the composite process score, distillation) and two hubs:

* :class:`NaiveEvoMapHub` - the public network's self-reported regime (promotion
  on the publisher's own validation report, +100 per promotion / +5 per fetch,
  GDI ranking from claimed metadata) for replicating "Behind EvoMap";
* :class:`SafeHub` - verified execution (vacuity lint + discriminative
  before/after check + task-bank uplift ``U_LCB >= delta``), adoption-based
  ranking (Beta LCB, exploration slots), adoption-only credits, and consumer-side
  quarantine with an RRSI-style re-test on the consumer's own held-out tasks.

Assets are compatible with GEP schema 1.14.0 (``asset_id`` hashing reproduces the
reference ``contentHash.js`` byte for byte). "EvoMap"/"GEP" are trademarks of
EvoMap; this is an independent implementation.

Quick start (any :class:`rsi.core.Domain` + seed harness :class:`rsi.core.Artifact`)::

    from rsi.evomap import Config, run, evaluate_library
    res = run(domain, harness, llm_task=model, llm_propose=proposer, config=Config(cycles=40, mode="safe"),
              out_dir="runs/genes")
    res.best["genes/library.json"]          # the evolved gene library
    evaluate_library(domain, model, res)    # no genes vs library on evolve / holdout / ood

Population level::

    from rsi.evomap import SafeHub, TaskBank, PopulationSimulator
    hub = SafeHub(TaskBank(domain, harness, reference_model, split="test"))
    sim = PopulationSimulator(domain, harness, hub, specs, ...); trace = sim.run(epochs=30)

Module map: ``hashing`` (canonical JSON + asset_id), ``schema`` (GEP 1.14.0 JSON
Schema subset validator), ``assets`` (Gene/Capsule/EvolutionEvent/Mutation/
ValidationReport/PersonalityState), ``store`` (LocalStore, quarantine zone,
audit, archive export/import), ``signals`` (extraction, pattern match, dedup,
plateau), ``memory`` (MemoryGraph advice, OutcomeInferrer), ``selector``
(GeneScorer, drift, GeneSelector), ``mutation`` (strategy presets, Mutation,
personality), ``validation`` (CommandPolicy, runners, VacuityDetector),
``solidify`` (constraints, composite score, Solidifier), ``distill`` (Distiller,
LeakageAuditor), ``prompts`` (gene/skill rendering, prompts), ``inject``
(injection interface, workspaces, GeneRoutedDomain), ``hub`` (Hub, Bundle,
CreditLedger, GDIRanker, NaiveEvoMapHub), ``safehub`` (TaskBank, SafeHub),
``quarantine`` (QuarantineGate), ``metrics`` (ReuseMetrics), ``agent``
(AgentNode), ``population`` (agent behaviours, PopulationSimulator), ``config``
+ ``loop`` (Config, run, evaluate_library).
"""
from .agent import AgentNode, Behavior, CycleResult
from .assets import Capsule, Clock, EvolutionEvent, Gene, Mutation, PersonalityState, ValidationReport, from_dict
from .config import Config
from .distill import Distiller, LeakageAuditor
from .hashing import SCHEMA_VERSION, asset_id, canonicalize, verify_asset_id
from .hub import (AssetRecord, AssetView, Bundle, CreditLedger, Decision, GDIRanker, Hub, NaiveEvoMapHub,
                  client_reuse_score)
from .inject import (AppendInjector, FileInjector, GeneRoutedDomain, Injector, SkillInjector, TextInjector,
                     library_artifact, read_library)
from .loop import evaluate_library, merged_usage, run
from .memory import Advice, MemoryGraph, OutcomeInferrer
from .metrics import ReuseMetrics
from .mutation import MutationBuilder, PersonalityModel, StrategyPolicy
from .population import AgentSpec, PopulationSimulator
from .prompts import parse_gene, render_gene, render_skill
from .quarantine import QuarantineGate, QuarantineResult
from .safehub import SafeHub, TaskBank, UpliftLCB, injection_lint
from .schema import JsonSchemaValidator
from .selector import GeneScorer, GeneSelector, drift_intensity
from .signals import (KeywordSignalScorer, PlateauDetector, RegexSignalExtractor, RunContext, SignalDeduper,
                      TaskSignalExtractor, match_pattern)
from .solidify import ConstraintChecker, CountedFilePolicy, RunState, Solidifier, composite_score
from .store import LocalStore
from .validation import (CommandPolicy, InProcessExecutor, SubprocessExecutor, ValidationRunner, VacuityDetector,
                         make_mutants)

__all__ = [
    "Config", "run", "evaluate_library", "merged_usage", "AgentNode", "Behavior", "CycleResult",
    "Gene", "Capsule", "EvolutionEvent", "Mutation", "ValidationReport", "PersonalityState", "Clock", "from_dict",
    "SCHEMA_VERSION", "canonicalize", "asset_id", "verify_asset_id", "JsonSchemaValidator", "LocalStore",
    "MemoryGraph", "Advice", "OutcomeInferrer", "GeneScorer", "GeneSelector", "drift_intensity",
    "SignalDeduper", "PlateauDetector", "RegexSignalExtractor", "KeywordSignalScorer", "TaskSignalExtractor",
    "RunContext", "match_pattern", "MutationBuilder", "StrategyPolicy", "PersonalityModel",
    "CommandPolicy", "ValidationRunner", "SubprocessExecutor", "InProcessExecutor", "VacuityDetector", "make_mutants",
    "Solidifier", "RunState", "ConstraintChecker", "CountedFilePolicy", "composite_score", "Distiller",
    "LeakageAuditor", "render_gene", "render_skill", "parse_gene", "Injector", "FileInjector", "AppendInjector",
    "SkillInjector", "TextInjector", "GeneRoutedDomain", "library_artifact", "read_library", "Hub", "Bundle",
    "Decision", "AssetRecord", "AssetView", "CreditLedger", "GDIRanker", "NaiveEvoMapHub", "client_reuse_score",
    "SafeHub", "TaskBank", "UpliftLCB", "injection_lint", "QuarantineGate", "QuarantineResult", "ReuseMetrics",
    "AgentSpec", "PopulationSimulator",
]
