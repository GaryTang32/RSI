"""AgentNode: one agent's evolution cycle on any rsi.core Domain (spec §3.1, §9.1).

A cycle: signals -> hub search (faithful: before local selection) -> local selection -> hub when nothing local fits
(safe: quarantine A/B first) -> solve with the gene injected, or write a new gene after a failed scratch attempt ->
solidify -> distil and publish.
"""
from __future__ import annotations

import hashlib
import random
import zlib
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional

from rsi.core import Artifact, Domain, Execution, Ledger, LLM, Node, Task, paired_diff_ci
from rsi.trace import RunTracer

from .assets import Gene, PersonalityState
from .config import Config
from .distill import Distiller, LeakageAuditor
from .hub import Bundle, Decision, client_reuse_score, register_agent, reuse_threshold
from .inject import FileInjector, Injector, library_artifact, post_workspace, pre_workspace, task_text
from .memory import Advice, Outcome, OutcomeInferrer
from .mutation import MutationBuilder, PersonalityModel, StrategyPolicy
from .prompts import GENE_WRITER_SYSTEM, gene_writer_prompt, parse_gene
from .quarantine import QuarantineGate
from .selector import GeneScorer, GeneSelector
from .signals import (HubSignalLayer, PlateauDetector, PlateauOverride, RunContext, SignalDeduper, TaskSignalExtractor,
                      has_error, is_problem, merge_signals, pattern_hits, signal_key)
from .solidify import (A2A_MAX_FILES, A2A_MAX_LINES, BROADCAST_SCORE, BROADCAST_STREAK, MIN_PUBLISH_SCORE,
                       ConstraintChecker, CountedFilePolicy, RunState, Solidifier)
from .store import LocalStore
from .tracing import eval_payload, gene_brief, injected_diff, loop_genes, trial_eval, validation_rows
from .validation import CommandPolicy, SubprocessExecutor, ValidationRunner, VacuityDetector

NAIVE_REUSE = ("reference", "direct", "replace")


@dataclass
class CycleResult:
    cycle: int
    task_id: str
    family: str
    signals: list
    gene_id: Optional[str]
    source: str                      # local | hub | generated | none
    reused_asset_id: Optional[str]
    task_score: float                # graded on the agent's own task (a new gene: its first retry)
    task_success: bool
    solidified: bool
    composite: float
    tokens: int
    proposer_calls: int
    hub_hit: bool = False
    quarantine: Optional[dict] = None
    published: Optional[str] = None
    event_id: str = ""
    selection_mode: str = ""
    banned: list = field(default_factory=list)
    distilled: Optional[str] = None
    validation_ok: bool = False
    n_validation_run: int = 0
    new_gene_retries: Optional[dict] = None   # a new gene's retry sample {"with": [...], "without": [...]}
    hub_gene_id: Optional[str] = None  # reference mode: the hub gene injected next to gene_id (never stored)

    def to_json(self) -> dict:
        return dict(self.__dict__)


class Behavior:
    """Publishing behaviour hooks (honest by default; see ``rsi.evomap.population``)."""

    kind = "honest"
    publishes = True

    def modify_bundle(self, bundle: Bundle, agent: "AgentNode") -> Bundle:
        return bundle


def _failure_reasons(res) -> list[str]:
    return (res.constraints.violations + res.protocol_violations + list((res.event.meta or {}).get("extra_failures")
                                                                        or [])
            + ([] if res.validation.ok else ["validation_failed"]))


class AgentNode:
    def __init__(self, name: str, domain: Domain, harness: Artifact, *, llm_task: Optional[LLM],
                 llm_propose: Optional[LLM] = None, config: Optional[Config] = None,
                 store: Optional[LocalStore] = None, hub=None, injector: Optional[Injector] = None,
                 extractor: Optional[TaskSignalExtractor] = None, ledger: Optional[Ledger] = None,
                 behavior: Optional[Behavior] = None, heldout_tasks: Optional[list] = None,
                 decision_tasks: Optional[list] = None, executor=None, counted: Optional[CountedFilePolicy] = None,
                 cluster: Optional[str] = None, tracer: Optional[RunTracer] = None, workspace=None) -> None:
        self.name = name
        self.tracer = tracer if tracer is not None else RunTracer(None, "evomap")   # write-only
        self._last_proposal: dict = {}
        self.domain, self.harness = domain, harness
        self.llm_task, self.llm_propose = llm_task, llm_propose
        self.cfg = cfg = (config or Config()).resolved()
        self.store = store or LocalStore(node_id=name)
        self.hub = hub
        self.injector = injector or FileInjector()
        self.extractor = extractor or TaskSignalExtractor()
        self.ledger = ledger if ledger is not None else Ledger()
        self.behavior = behavior or Behavior()
        self.rng = random.Random(f"{cfg.seed}-{name}")
        self.seed_base = zlib.crc32(f"{cfg.seed}-{name}".encode()) % 10_000 * 1000
        executor = executor or (domain.validation_executor() if callable(getattr(domain, "validation_executor", None))
                                else SubprocessExecutor())
        if cfg.mode == "faithful":
            self.runner = ValidationRunner(CommandPolicy.faithful(), executor, mode="faithful")
        else:
            self.runner = ValidationRunner(CommandPolicy.safe(), executor, mode="safe",
                                           internal={"rsi-taskcheck": self._taskcheck})
        vac = VacuityDetector(self.runner) if cfg.vacuity_check else None
        self.counted = counted or getattr(domain, "counted_policy", None) or CountedFilePolicy()
        self.solidifier = Solidifier(self.store, self.runner, mode=cfg.mode, constraints=ConstraintChecker(),
                                     counted=self.counted, vacuity=vac, require_task_success=cfg.require_task_success,
                                     rollback=cfg.rollback, estimate_drift_penalty=cfg.estimate_drift_penalty,
                                     workspace=workspace)
        self.selector = GeneSelector(GeneScorer(cfg.selector_mode, require_match=cfg.require_match),
                                     use_memory=cfg.use_memory)
        self.deduper = SignalDeduper()
        # layer 3 is empty without a hub analyzer, as in Evolver without a hub
        analyzer = getattr(hub, "analyze_signals", None) if hub is not None else None
        self.llm_signals = HubSignalLayer(analyzer, sender_id=name) if cfg.llm_signal_layer else None
        self.plateau = PlateauDetector()
        self.policy = StrategyPolicy(cfg.strategy_preset)
        self.mutations = MutationBuilder()
        self.personality = PersonalityModel(PersonalityState())
        self.inferrer = OutcomeInferrer(cfg.outcome_source)
        self.distiller = Distiller(mode=cfg.mode, every=cfg.distill_every, min_capsules=cfg.distill_min_capsules,
                                   command_policy=self.runner.policy)
        self.auditor = LeakageAuditor(domain.leakage_terms(cfg.split) if cfg.split in domain.tasks.splits else ())
        self.decision_tasks = decision_tasks if decision_tasks is not None else self._split(cfg.split)
        ho = heldout_tasks if heldout_tasks is not None else (self._split(cfg.heldout_split) or self.decision_tasks)
        self.quarantine = QuarantineGate(domain, harness, llm_task, ho, injector=self.injector,
                                         extractor=self.extractor, k=cfg.quarantine_k, n_max=cfg.quarantine_n,
                                         min_tasks=cfg.quarantine_min_tasks, gate=cfg.quarantine_gate,
                                         delta=cfg.quarantine_delta, seed=cfg.seed) if hub is not None else None
        self.cluster = cluster or name
        if hub is not None:
            register_agent(hub, name, self.cluster)
        self.t = self._rollouts = self.proposer_calls = self.n_quarantined = self.n_quarantine_rejected = 0
        self.results: list[CycleResult] = []
        self._pending: Optional[dict] = None
        self._last_trace = ""
        self._published: set = set()
        self._reviewed: set = set()        # naive consumers review each hub asset once
        self._rejected: set = set()        # safe consumers never re-test an asset their quarantine rejected
        self._gene_history: list[dict] = []   # genes tried + measured outcome, for the gene-writer prompt
        self.last_gene: Optional[Gene] = None   # last cycle's hub gene if one was injected, else its solidified gene
        self.last_genes: list = []               # every gene injected into the last cycle's solve

    def _split(self, name: Optional[str]) -> list:
        """Tasks of a decision split; a sealed split raises SealedSplitError (it must never steer decisions)."""
        if not name or name not in self.domain.tasks.splits:
            return []
        return self.domain.tasks.split(name)

    def _run(self, task: Task, genes: list, seed: int):
        art = self.injector.inject(self.harness, genes) if genes else self.harness
        self._rollouts += 1
        return self.domain.run(art, task, seed=seed, llm=self.llm_task)

    @property
    def n_rollouts(self) -> int:
        """Task rollouts paid for: solves, rsi-taskcheck A/Bs and quarantine re-tests."""
        return self._rollouts + (self.quarantine.ev.n_rollouts if self.quarantine is not None else 0)

    def _gene_successes(self) -> dict:
        out: dict = {}
        for e in self.store.events:
            if e.outcome.get("status") == "success":
                for g in e.genes_used:
                    out[g] = out.get(g, 0) + 1
        return out

    def _recent_success(self) -> list[bool]:
        return [e.outcome.get("status") == "success" for e in self.store.recent_events(8)]

    def _taskcheck(self, argv, files, ctx) -> tuple[int, str]:
        """``rsi-taskcheck [--n N]``: gene vs no gene on in-scope decision tasks (paired seeds); exit 0 iff the
        paired bootstrap lower bound of the per-task improvement is > 0."""
        gene = (ctx or {}).get("gene")
        if gene is None:
            return 1, "no gene in context"
        n = self.cfg.taskcheck_n
        if "--n" in argv:
            try:
                n = int(argv[argv.index("--n") + 1])
            except (ValueError, IndexError):
                pass
        scope = [t for t in self.decision_tasks
                 if pattern_hits(gene.signals_match, self.extractor.extract(RunContext(task=t))) > 0]
        if not scope:
            return 1, "no in-scope decision tasks"
        scope = scope[:n]
        k = self.cfg.taskcheck_k
        with_g = [sum(self._run(t, [gene], self.seed_base + s).score for s in range(k)) / k for t in scope]
        without = [sum(self._run(t, [], self.seed_base + s).score for s in range(k)) / k for t in scope]
        if self.tracer.enabled:
            for arm, vals in (("with", with_g), ("without", without)):
                self.tracer.event("eval", self.t, candidate=f"taskcheck:{arm}:{gene.id}",
                                  summary={"split": self.cfg.split, "S": sum(vals) / len(vals), "n_tasks": len(vals),
                                           "k": k, "errors": 0, "missing": 0},
                                  per_task={t.id: v for t, v in zip(scope, vals)},
                                  stage="rsi-taskcheck (validation command): gene vs no gene, paired seeds")
        d = paired_diff_ci(without, with_g, alpha=self.cfg.taskcheck_alpha, reps=1000) if len(scope) > 1 else \
            {"mean_diff": with_g[0] - without[0], "lo": with_g[0] - without[0]}
        ok = d["mean_diff"] > 0 and d["lo"] > 0
        return (0 if ok else 1), (f"taskcheck n={len(scope)} k={k} with={sum(with_g) / len(with_g):.3f} "
                                  f"without={sum(without) / len(without):.3f} paired LCB={d['lo']:+.3f}")

    def _proposer_tokens(self) -> int:
        if self.llm_propose is None:
            return 0
        snap = self.llm_propose.meter.snapshot()
        return int(sum(v.get("total_tokens", 0) for k, v in snap.items() if k.split(":")[0] == "proposer"))

    def _record_pending(self, cur_error: bool) -> None:
        p, self._pending = self._pending, None
        oc = self.inferrer.infer(prev_error=p["had_error"], cur_error=cur_error, transcript=p["transcript"],
                                 measured=p["measured"], recent_success=self._recent_success())
        self.store.memory.record_outcome(signals=p["signals"], gene_id=p["gene_id"], status=oc.status, score=oc.score,
                                         note=oc.note, observed=oc.observed, predictive=oc.predictive)

    def flush(self) -> None:
        """Write a pending next-cycle outcome now (end of a run)."""
        if self._pending is not None:
            self._record_pending(cur_error=False)

    def _consult_hub(self, signals: list, task: Task):
        """(gene, asset_id, quarantine dict, hub_hit): naive consumers take the best hit above the reuse threshold;
        safe consumers stage it and promote it only if their quarantine A/B passes."""
        cfg, hub, tr = self.cfg, self.hub, self.tracer
        views = [v for v in hub.search(signals, k=cfg.hub_k, consumer=self.name) if v.author != self.name]
        if tr.enabled:
            what = {"first": "hub search first (Evolver enrich stage, before local selection)",
                    "always": "hub search"}.get(cfg.hub_when, "hub search (nothing local fits)")
            tr.event("note", self.t, what=what, signals=list(signals),
                     views=[{"asset_id": v.asset_id[:24], "gene": (v.gene or {}).get("id"), "author": v.author,
                             "status": v.status, "rank_score": v.score, "similarity": v.similarity,
                             "exploration": v.exploration, "already_rejected": v.asset_id in self._rejected}
                            for v in views])
        miss = None, None, None, False
        if not views:
            return miss
        if cfg.reuse_mode in NAIVE_REUSE:
            best = max(views, key=lambda v: (client_reuse_score(v), v.score))
            if client_reuse_score(best) < reuse_threshold(signals, cfg.reuse_threshold, cfg.reuse_threshold_problem):
                return miss
            b = hub.fetch(best.asset_id, self.name)
            return miss if b is None else (Gene.from_dict(b.gene), best.asset_id, None, True)
        if cfg.reject_memory:
            views = [v for v in views if v.asset_id not in self._rejected]
            if not views:
                return miss
        v = views[0]
        b = hub.fetch(v.asset_id, self.name)
        if b is None:
            return miss
        try:
            self.store.stage_external(b.gene, source=hub.name, hub_report_id=(v.asset_id or "")[:24])
        except ValueError:
            self._rejected.add(v.asset_id)
            return None, v.asset_id, {"promote": False, "reason": "asset_id integrity check failed"}, True
        self.n_quarantined += 1
        q = self.quarantine.test(Gene.from_dict(b.gene))
        qd = {"promote": q.promote, "reason": q.reason, "dS": q.dS, "delta": q.delta, "n": q.n}
        if tr.enabled:
            self._trace_quarantine(v, b, q)
        if q.promote:
            g = self.store.promote_external(v.asset_id, validated=True)
            row = hub.report_outcome(v.asset_id, self.name, 1, q.proof)
            if tr.enabled:
                tr.event("note", self.t, what="adoption report sent to hub (outcome=1)", asset_id=v.asset_id[:24],
                         hub_row=row, promoted_local_gene=g.id if g is not None else None)
            return g, v.asset_id, qd, True
        self.n_quarantine_rejected += 1
        self._rejected.add(v.asset_id)
        self.store.resolve_external(v.asset_id, "rejected", q.reason)
        if q.n > 0:
            row = hub.report_outcome(v.asset_id, self.name, 0, q.proof)
            if tr.enabled:
                tr.event("note", self.t, what="adoption report sent to hub (outcome=0)", asset_id=v.asset_id[:24],
                         hub_row=row)
        return None, v.asset_id, qd, True

    def _trace_quarantine(self, v, b, q) -> None:
        tr, gid = self.tracer, (b.gene or {}).get("id")
        last = self.quarantine.last
        if "base" in last:
            for cand, r in (("quarantine:no_gene", last["base"]), (f"quarantine:{gid}", last["cand"])):
                tr.event("eval", self.t, **eval_payload(cand, r, stage="consumer quarantine A/B, own held-out tasks"))
        tr.event("critic", self.t, candidate=f"hub:{gid}", accept=q.promote, stage="consumer quarantine",
                 objections=[] if q.promote else [q.reason], asset_id=v.asset_id[:24], author=v.author)
        tr.gate(self.t, f"quarantine:{gid}", q.promote, q.reason,
                math={"rule": f"{self.quarantine.gate}: RRSIGate(floor S* - delta, cost rule) AND dS > 0"
                      if self.quarantine.gate == "rrsi" else "paired LCB > 0",
                      "S_no_gene": q.S_base, "S_gene": q.S_gene, "dS": q.dS, "delta": q.delta, "n_tasks": q.n,
                      "k": self.quarantine.k, "min_tasks": self.quarantine.min_tasks,
                      "C_no_gene": last["base"].cost if "base" in last else None,
                      "C_gene": last["cand"].cost if "cand" in last else None})

    def _propose(self, task: Task, signals: list, trial) -> Optional[Gene]:
        if self.llm_propose is None:
            return None
        pub = ""
        hook = getattr(self.domain, "public_feedback", None)
        if callable(hook):
            pub = str(hook(task, Execution(output=trial.output, trace=trial.trace, meta=trial.meta or {})))
        trace = (trial.trace or "")[-1500:] + ("\nPublic checks:\n" + pub if pub else "") + \
            f"\nResult: {'solved' if trial.score >= self.cfg.success_threshold else 'NOT solved'}"
        # §6.1 history block: earlier genes in scope of these signals, with their measured outcome
        hist = [h for h in self._gene_history if pattern_hits(h["signals_match"], signals) > 0
                or set(h["task_signals"]) & {s for s in signals if s.startswith("task:")}]
        prompt = gene_writer_prompt(signals, task_text(self.domain, task), trace,
                                    validation_hint=self.cfg.validation_hint, history=hist)
        self.proposer_calls += 1
        resp = self.llm_propose.complete(prompt, system=GENE_WRITER_SYSTEM, seed=self.seed_base + self.t,
                                         role="proposer")
        self._last_proposal = {"prompt": prompt, "system": GENE_WRITER_SYSTEM, "reply": resp.text}
        task_sig = [s for s in signals if s.startswith("task:")]
        g = parse_gene(resp.text, default_id=f"gene_{self.name}_{self.t}", default_signals=task_sig,
                       default_validation=self.cfg.default_validation)
        if g is None:
            return None
        if not g.validation and self.cfg.default_validation:
            g.validation = list(self.cfg.default_validation)
        if g.id in self.store.genes:
            g.id = f"{g.id}_{self.name}_{self.t}"
        g.provenance = {"kind": "evolved", "author": self.name, "cycle": self.t}
        leak = self.auditor.audit(g, public_text=task_text(self.domain, task),
                                  hidden_text=f"{task.target}\n{trial.feedback}")
        self._last_proposal["leakage_audit"] = getattr(leak, "__dict__", str(leak))
        return g

    def _write_gene(self, task: Task, signals: list, trial, ref: list) -> Optional[Gene]:
        self._last_proposal = {}
        g_new = self._propose(task, signals, trial)
        if self.tracer.enabled and self.llm_propose is not None:
            lp = self._last_proposal
            art = self.injector.inject(self.harness, [g_new] + ref) if g_new is not None else self.harness
            self.tracer.proposal(self.t, g_new.id if g_new else "unparsed", parent=self.store.gene_library_version(),
                                 prompt=lp.get("prompt", ""), reply=lp.get("reply", ""), system=lp.get("system", ""),
                                 change=f"gene writer wrote new gene {g_new.id}" if g_new else "",
                                 hypothesis=g_new.summary if g_new else "",
                                 components=[f"gene:{g_new.id}"] if g_new else [],
                                 diff=injected_diff(self.harness, art),
                                 error=None if g_new else "gene writer reply did not parse into a gene",
                                 source="generated", gene=gene_brief(g_new), leakage_audit=lp.get("leakage_audit"))
        return g_new

    def _retry_new_gene(self, task: Task, gene: Gene, ref: list, seed: int) -> tuple:
        """Judge a new gene on a sample of its own task (A1). ``single``: fresh-seed retries, solved iff more than
        half solve; ``paired``: the same seeds also run without it and the gene must win more often.

        Returns (trial solidify sees, extra tokens, retry info, realized outcome = the first retry's score)."""
        cfg, tr = self.cfg, self.tracer
        n_ret = max(1, int(cfg.new_gene_retries or 1))
        paired = cfg.new_gene_check == "paired"
        trials, bare = [], []
        for i in range(n_ret):
            sd = seed + 7919 * (i + 1)
            trials.append(self._run(task, [gene] + ref, sd))
            if tr.enabled:
                tr.event("eval", self.t, **trial_eval(f"solve:{gene.id}", cfg.split, task, trials[-1], seed=sd,
                                                      attempt=f"retry {i + 1}/{n_ret} with the new gene"))
            if paired:
                bare.append(self._run(task, list(ref), sd))
                if tr.enabled:
                    tr.event("eval", self.t, **trial_eval("solve:no_gene", cfg.split, task, bare[-1], seed=sd,
                                                          attempt=f"paired retry {i + 1}/{n_ret} without a gene"))
        wins = [t.score >= cfg.success_threshold for t in trials]
        solved = sum(wins) > len(wins) / 2
        if paired:
            solved = solved and sum(wins) > sum(t.score >= cfg.success_threshold for t in bare)
        info = {"with": [t.score for t in trials], "without": [t.score for t in bare], "check": cfg.new_gene_check,
                "solved": solved}
        trial = next((t for t, w in zip(trials, wins) if w == solved), trials[0])
        extra = sum(t.tokens for t in trials) - trial.tokens + sum(t.tokens for t in bare)
        return trial, extra, info, trials[0].score

    def _snapshot(self) -> dict:
        st, mem = self.store, self.store.memory
        d = {"cycles_done": self.t, "library": sorted(g.id for g in loop_genes(st)),
             "library_version": st.gene_library_version(), "n_capsules": len(st.capsules),
             "n_failed_capsules": len(st.failed_capsules), "n_events": len(st.events),
             "last_event": st.last_event_id(),
             "recent_outcomes": [e.outcome.get("status") for e in st.recent_events(8)],
             "memory_graph": dict(Counter(e.get("kind") for e in mem.events)),
             "pending_outcome_gene": (self._pending or {}).get("gene_id") if self._pending else None,
             "personality": self.personality.state.to_dict() if hasattr(self.personality, "state") else None,
             "rollouts": self.n_rollouts, "proposer_calls": self.proposer_calls,
             "quarantined": self.n_quarantined, "quarantine_rejected": self.n_quarantine_rejected,
             "rejected_assets": len(self._rejected), "published_keys": len(self._published),
             "external_candidates": len(st.external)}
        hub = self.hub
        if hub is not None and hasattr(hub, "records"):
            d["hub"] = {"assets_by_status": dict(Counter(r.status for r in hub.records.values())),
                        "credits": dict(getattr(hub.credits, "balance", {}) or {})}
        return d

    def cycle(self, task: Task, seed: Optional[int] = None) -> CycleResult:
        tr = self.tracer
        if not tr.enabled:
            return self._cycle(task, seed)
        t = self.t + 1
        genes0 = loop_genes(self.store)
        lib0 = library_artifact(self.harness, genes0)
        n_out0 = sum(1 for e in self.store.memory.events if e.get("kind") == "outcome")
        tr.event("round_start", t, task=task.id, family=task.family, **self._snapshot())
        cr = self._cycle(task, seed)
        genes1 = loop_genes(self.store)
        lib1 = library_artifact(self.harness, genes1)
        new = sorted({g.id for g in genes1} - {g.id for g in genes0})
        if new:
            why = f"solidify succeeded: new gene(s) {new} ({cr.source}) added to the library"
        elif cr.solidified:
            why = (f"solidify succeeded with existing gene {cr.gene_id}: library unchanged except its learning "
                   "record (learning_history / epigenetic marks / widened signals)")
        elif cr.gene_id is None and cr.task_success:
            why = "task solved without a gene: nothing to solidify (safe-mode skip); library unchanged"
        elif cr.gene_id is None and cr.hub_gene_id is None:
            why = ("task not solved and no gene available (no local match, no hub asset adopted, no gene writer "
                   "or no parsable gene): a failed event is recorded; library unchanged")
        else:
            ref = f" (hub reference {cr.hub_gene_id} is never stored)" if cr.hub_gene_id else ""
            why = (f"solidify failed ({cr.source} gene {cr.gene_id}): change rolled back; "
                   + ("gene not stored" if cr.source == "generated" or (cr.source == "hub" and not cr.hub_gene_id)
                      else "only its failure record changed") + ref)
        tr.decision(t, kept=(new[0] if new else None), incumbent_before=lib0.short_id, incumbent_after=lib1.short_id,
                    why=why, library_before=[g.id for g in genes0], library_after=[g.id for g in genes1],
                    library_diff=lib0.diff(lib1)[:3000] if lib0.id != lib1.id else "")
        outs = [e for e in self.store.memory.events if e.get("kind") == "outcome"]
        tr.event("state", t, **self._snapshot(), memory_outcomes_written=[
            {"gene": o.get("gene", {}).get("id"), "outcome": o.get("outcome")} for o in outs[n_out0:]],
            cycle_result=cr.to_json())
        return cr

    def _trace_analysis(self, task: Task, base_sig: list, dd, signals: list, plateau, drift: bool, advice, dec,
                        hub_first: bool) -> None:
        cfg = self.cfg
        top = sorted(dec.scores.items(), key=lambda kv: -kv[1])[:6] if isinstance(dec.scores, dict) else []
        text = (f"task {task.id} (family {task.family}); signals {signals}"
                + (f" (deduper suppressed {dd.suppressed})" if dd.suppressed else "")
                + f"; plateau={'%s/%s' % (plateau.active, plateau.severity)}; drift={drift}; "
                f"memory advice: preferred={advice.preferred_gene_id}, banned={sorted(advice.banned_gene_ids)}; "
                f"local selector: mode={dec.mode} -> {dec.gene.id if dec.gene else 'none'}"
                + (f" (top scores {top})" if top else "") + (f"; banned {sorted(dec.banned)}" if dec.banned else "")
                + ("; hub searched first (Evolver enrich stage)" if hub_first else
                   "; hub will be consulted" if self.hub is not None and (cfg.hub_when == "always" or dec.gene is None)
                   else ""))
        self.tracer.event("analysis", self.t, text=text, signals_raw=base_sig, signals=list(signals),
                          dedup={"suppressed": dd.suppressed, "notes": dd.notes, "ban_gene": dd.ban_gene},
                          plateau={"active": plateau.active, "severity": plateau.severity}, drift=drift,
                          advice={"preferred": advice.preferred_gene_id, "banned": sorted(advice.banned_gene_ids),
                                  "ban_reasons": advice.ban_reasons, "total_attempts": advice.total_attempts,
                                  "scores": advice.scores},
                          selector={"mode": dec.mode, "gene": dec.gene.id if dec.gene else None,
                                    "capsule": dec.capsule.id if dec.capsule else None,
                                    "alternatives": dec.alternatives, "reasons": dec.reasons, "scores": dec.scores,
                                    "scores_note": "base + adjustment per gene, BEFORE the x1.5 memory-preference "
                                                   "factor (applied to the preferred gene when ranking; see reasons)",
                                    "banned": sorted(dec.banned),
                                    "drift_intensity": dec.drift_intensity, "memory_used": dec.memory_used})

    def _solve(self, task: Task, injected: list, seed: int, gene: Optional[Gene], hub_ref: Optional[Gene],
               source: str, reused, sel_mode: str, ctx: dict):
        cfg, tr = self.cfg, self.tracer
        if not injected:
            if tr.enabled:
                tr.proposal(self.t, "no_gene", parent=self.store.gene_library_version(),
                            change="no gene fits: solve from scratch with the bare harness", diff="",
                            source="none", **ctx)
            trial = self._run(task, [], seed)
            if tr.enabled:
                tr.event("eval", self.t, **trial_eval("solve:no_gene", cfg.split, task, trial, seed=seed,
                                                      attempt="scratch"))
            return trial
        ids = "+".join(g.id for g in injected)
        if tr.enabled:
            art = self.injector.inject(self.harness, injected)
            if hub_ref is not None:
                change = (f"hub asset {str(reused)[:24]} (gene {hub_ref.id}) injected as a STRONG REFERENCE "
                          + (f"next to local gene {gene.id} (selector {sel_mode})" if gene is not None else
                             "(no local gene fits)"))
            elif source == "local":
                change = f"reuse local gene {gene.id} (selector {sel_mode})"
            else:
                change = (f"use hub asset {str(reused)[:24]} as gene {gene.id} "
                          + ("(quarantine promoted it)" if cfg.reuse_mode == "quarantine" else
                             f"({cfg.reuse_mode} reuse)"))
            tr.proposal(self.t, ids, parent=self.store.gene_library_version(), change=change,
                        hypothesis=injected[0].summary, components=[f"gene:{g.id}" for g in injected],
                        diff=injected_diff(self.harness, art), source=source, gene=gene_brief(injected[0]),
                        **({"hub_gene": gene_brief(hub_ref)} if hub_ref is not None else {}), **ctx)
        trial = self._run(task, injected, seed)
        if tr.enabled:
            tr.event("eval", self.t, **trial_eval("solve:" + ids, cfg.split, task, trial, seed=seed,
                                                  attempt="with the selected gene(s)"))
        return trial

    def _cycle(self, task: Task, seed: Optional[int] = None) -> CycleResult:
        cfg, st, tr = self.cfg, self.store, self.tracer
        self.t += 1
        st.clock.tick()
        seed = self.seed_base + self.t if seed is None else seed
        recent = st.recent_events(80)
        corpus = self._last_trace if cfg.carry_log_signals else ""   # faithful: the previous attempt's log
        base_sig = self.extractor.extract(RunContext(task=task, corpus=corpus))
        if self.llm_signals is not None:
            base_sig = merge_signals(base_sig, self.llm_signals.extract(corpus))
        if self._pending is not None:
            self._record_pending(cur_error=has_error(base_sig))
        dd = self.deduper.apply(base_sig, recent)
        signals = dd.signals
        st.memory.record("signal", signal={"key": signal_key(signals), "signals": list(signals),
                                           "error_signature": next((x for x in signals if x.startswith("errsig:")),
                                                                   None)})
        hub_first = self.hub is not None and cfg.hub_when == "first"   # Evolver: hub search before selection
        hres = None
        if hub_first:
            hres = self._consult_hub(signals, task)
            if hres[0] is None and is_problem(signals) and "hub_search_miss_with_problem" not in signals:
                signals = signals + ["hub_search_miss_with_problem"]
        plateau = self.plateau.override(recent) if cfg.plateau_override else PlateauOverride(False)
        drift = cfg.drift or (plateau.active and plateau.severity == "required")
        if plateau.active:
            self.personality.force_pivot(plateau.severity)
        advice = st.memory.advice(signals, st.genes.values(), mode=cfg.memory_mode, key_match=cfg.memory_key_match) \
            if cfg.use_memory else Advice()
        dec = self.selector.select(list(st.genes.values()), list(st.capsules.values()), signals, advice=advice,
                                   failed_capsules=st.failed_capsules if cfg.failed_capsule_bans else [],
                                   rng=self.rng, drift_enabled=drift, plateau=plateau,
                                   env=cfg.env if cfg.epigenetic_suppression else None,
                                   gene_successes=self._gene_successes() if cfg.failed_capsule_rule == "relative"
                                   else None)
        gene, source, reused, qd, hub_hit = dec.gene, ("local" if dec.gene is not None else "none"), None, None, False
        if tr.enabled:
            self._trace_analysis(task, base_sig, dd, signals, plateau, drift, advice, dec, hub_first)
        if self.hub is not None and not hub_first and (cfg.hub_when == "always" or gene is None):
            hres = self._consult_hub(signals, task)
        hub_ref: Optional[Gene] = None
        if hres is not None:
            hg, aid, qd, hub_hit = hres
            if hg is not None and cfg.reuse_mode == "reference":
                # Evolver default: the hit sits NEXT TO the selected gene; solidify scores only the local gene
                hub_ref, source, reused = hg, "hub", aid
            elif hg is not None:
                gene, source, reused = hg, "hub", aid
        pol = self.policy.adaptive(recent, gene, signals, self.t)
        pers = self.personality.select_for_run(drift, signals, self._recent_success())
        # §3.4: innovate also when creative and the last 6 outcomes all succeeded with mean score >= 0.7
        last6 = [e.outcome for e in st.recent_events(6)]
        creative = (pers.creativity >= 0.75 and len(last6) == 6 and all(o.get("status") == "success" for o in last6)
                    and sum(float(o.get("score", 0.0)) for o in last6) / 6 >= 0.7)
        if creative and "stable_success_plateau" not in signals:
            signals = signals + ["stable_success_plateau"]
        allow_high = (drift and self.personality.known and pers.rigor >= 0.8 and pers.risk_tolerance <= 0.3
                      and "log_error" not in signals)
        # buildMutation resolves the preset without signals
        mut = self.mutations.build(signals, gene, innovate_mode=drift or pol.force_innovate or creative,
                                   personality=pers, allow_high_risk=allow_high, preset=self.policy.resolve(self.t),
                                   clock_s=st.clock.now())
        gid0 = gene.id if gene is not None else None
        st.memory.record("hypothesis", gene={"id": gid0},
                         hypothesis={"id": f"hyp_{self.name}_{self.t:06d}", "predicted_outcome": "success",
                                     "text": f"Given signal_key={signal_key(signals)[:160]} with {len(signals)} "
                                             f"signals, selecting gene={gid0} under mode={mut.category} is expected "
                                             "to reduce repeated errors and improve stability."})
        st.memory.record("attempt", gene={"id": gid0}, action={"id": f"act_{self.name}_{self.t:06d}", "drift": drift,
                                                              "selected_by": source, "selector": dec.mode})
        ptok0 = self._proposer_tokens()   # proposer role only: solve calls are counted via trial.tokens
        tokens = 0
        ctx = {"mutation": mut.to_dict() if hasattr(mut, "to_dict") else str(mut),
               "personality": pers.to_dict() if hasattr(pers, "to_dict") else str(pers),
               "policy": {"preset": pol.preset, "max_files": pol.max_files, "force_innovate": pol.force_innovate,
                          "cautious": pol.cautious, "directives": pol.directives}}
        retry_scores = outcome_score = retry_info = solved_override = None
        injected = ([gene] if gene is not None else []) + ([hub_ref] if hub_ref is not None else [])
        used = list(injected)
        trial = self._solve(task, injected, seed, gene, hub_ref, source, reused, dec.mode, ctx)
        # write a gene when no LOCAL gene fits and the attempt failed; a hub reference stays next to it
        if gene is None and cfg.propose and trial.score < cfg.success_threshold:
            ref = [hub_ref] if hub_ref is not None else []
            g_new = self._write_gene(task, signals, trial, ref)
            if g_new is not None:
                gene, source = g_new, "generated"
                used = [gene] + ref
                if cfg.retry_after_propose:
                    tokens += trial.tokens
                    trial, extra, retry_info, outcome_score = self._retry_new_gene(task, gene, ref, seed)
                    retry_scores, solved_override = retry_info["with"], retry_info["solved"]
                    tokens += extra
        tokens += trial.tokens
        if self.llm_propose is not None:
            tokens += self._proposer_tokens() - ptok0
        solved = trial.score >= cfg.success_threshold if solved_override is None else solved_override
        self.last_genes = list(used)
        self.last_gene = hub_ref if hub_ref is not None else gene
        if gene is None and solved and cfg.skip_geneless_success:
            if tr.enabled:
                tr.gate(self.t, "no_gene", False, "safe mode: task solved without a gene -> nothing to solidify "
                        "(skipped: no event, no capsule, no memory outcome)",
                        math={"task_score": trial.score, "success_threshold": cfg.success_threshold})
            return self._skip(task, signals, dec, trial, tokens, reused, qd, hub_hit)
        if gene is None and cfg.mode == "faithful":
            gene = self._auto_gene(signals)
            source = "auto" if hub_ref is None else source
        ex = Execution(output=trial.output, trace=trial.trace, meta=trial.meta or {}, error=trial.error)
        before = pre_workspace(self.domain, task)
        after = post_workspace(self.domain, task, ex) if trial.error is None else dict(before)
        is_new = gene is not None and gene.id not in st.genes
        as_reference = hub_ref is not None or (source == "hub" and cfg.reuse_mode == "replace")
        rs = RunState(run_id=f"run_{self.name}_{self.t}", signals=signals, gene=gene, mutation=mut, personality=pers,
                      before=before, after=after, capsule=dec.capsule, estimate=pol.blast_estimate,
                      source_type="reference" if as_reference else "reused" if source == "hub" else "generated",
                      reused_asset_id=reused, personality_known=self.personality.known, task_id=task.id,
                      task_success=solved,
                      hidden_score=trial.score if retry_scores is None else sum(retry_scores) / len(retry_scores),
                      env=cfg.env, validation_context={"gene": gene, "task": task})
        res = self.solidifier.solidify(rs)
        if gene is not None:
            plain = {"task_check_failed": "the agent's own task was still NOT solved with this gene",
                     "validation_failed": "its validation commands failed"}
            why = "" if res.success else ", ".join(plain.get(x, x) for x in _failure_reasons(res)[:3])
            self._gene_history = (self._gene_history + [{
                "id": gene.id, "signals_match": list(gene.signals_match), "summary": gene.summary,
                "strategy": list(gene.strategy), "avoid": list(gene.avoid), "why": why,
                "task_signals": [s for s in signals if s.startswith("task:")],
                "outcome": "KEPT (own task solved)" if res.success else "FAILED"}])[-20:]
        if tr.enabled:
            self._trace_solidify(rs, res, gene, solved, trial, retry_info)
        if res.success and is_new:
            if source == "hub" and reused:
                gene.parent = reused
            st.upsert_gene(gene)
            self.ledger.add(Node(id=f"gene:{gene.id}", parent=None, round=self.t, kind="gene", status="accepted",
                                 change=f"{source} gene {gene.id}", artifact_id=gene.asset_id,
                                 meta={"source": source, "reused_asset_id": reused}))
        measured = Outcome(res.event.outcome["status"], res.score, "solidify_measured", observed=True)
        if cfg.outcome_timing == "immediate":
            if gene is not None:
                st.memory.record_outcome(signals=signals, gene_id=gene.id, status=measured.status,
                                         score=measured.score, note=measured.note, observed=True)
        else:
            self._pending = {"signals": signals, "gene_id": gene.id if gene else None, "transcript": trial.trace or "",
                             "had_error": has_error(base_sig), "measured": measured}
        self._last_trace = (trial.trace or "")[-4000:]
        self.personality.update_stats(pers, res.success, res.score)
        if self.hub is not None and reused and cfg.reuse_mode in NAIVE_REUSE and reused not in self._reviewed:
            self._reviewed.add(reused)   # §4.17: naive consumers self-assess one review per asset
            self.hub.report_outcome(reused, self.name, int(res.success),
                                    {"score": res.score, "violations": res.constraints.violations})
        distilled = self._distill(res)
        published = None
        # safe: an adopted hub gene (provenance external) is never re-published under this agent's name
        adopted = cfg.mode == "safe" and gene is not None and (gene.provenance or {}).get("kind") == "external"
        if self.hub is not None and cfg.publish and self.behavior.publishes and res.success and gene is not None \
                and rs.source_type != "reused" and not adopted \
                and (res.publishable or not cfg.publish_requires_eligibility):
            published = self.publish(gene, res.capsule, res.event, res.validation.report.to_dict(), before, after)
        rid = res.event.id
        self.ledger.add(Node(id=rid, parent=res.event.parent, round=self.t, kind="cycle",
                             status="success" if res.success else "failed", score=res.score, cost=float(tokens),
                             change=f"{source}:{gene.id if gene else '-'} on {task.id}",
                             artifact_id=gene.asset_id if gene is not None else None,
                             metrics={"task_score": trial.score, "composite": res.score, "tokens": tokens},
                             meta={"signals": signals[:12], "selection": dec.mode, "reused": reused,
                                   "quarantine": qd, "published": published, "distilled": distilled,
                                   "violations": res.constraints.violations}))
        o_score = trial.score if outcome_score is None else outcome_score
        cr = CycleResult(self.t, task.id, task.family, signals, gene.id if gene else None, source, reused,
                         o_score, o_score >= cfg.success_threshold if outcome_score is not None else solved,
                         res.success, res.score, int(tokens), self.proposer_calls, hub_hit, qd,
                         published, rid, dec.mode, sorted(dec.banned), distilled, res.validation.ok,
                         res.validation.n_run, retry_info)
        cr.hub_gene_id = hub_ref.id if hub_ref is not None else None
        self.results.append(cr)
        return cr

    def _distill(self, res) -> Optional[str]:
        """Run the distillers after a successful solidify; returns the produced gene id(s), comma-joined."""
        cfg, tr, st = self.cfg, self.tracer, self.store
        distilled, dr = None, None
        if res.success and cfg.distill:
            dr = self.distiller.maybe_distill(st, llm=self.llm_propose if cfg.llm_distill else None,
                                              failures=bool(cfg.failure_distill))
            if dr is not None and dr.ok and dr.gene is not None:
                distilled = dr.gene.id
            fr = self.distiller.last_failure_result
            if fr is not None and fr.ok and fr.gene is not None:
                distilled = f"{distilled},{fr.gene.id}" if distilled else fr.gene.id
        if tr.enabled and distilled:
            tr.event("note", self.t, what="distiller produced gene(s)", genes=distilled)
        elif tr.enabled and res.success and cfg.distill and dr is not None:
            tr.event("note", self.t, what="distiller triggered but produced no gene", ok=dr.ok,
                     reason=getattr(dr, "reason", ""), solidify_count=getattr(st, "solidify_count", None))
        return distilled

    def _trace_solidify(self, rs, res, gene, solved: bool, trial, retry_info: Optional[dict] = None) -> None:
        cfg, meta, val = self.cfg, res.event.meta or {}, res.validation
        reason = ("keep: constraints ok, validation ok" + (", non-vacuous" if res.vacuity is not None else "")
                  + (", own task solved" if cfg.require_task_success else "")) if res.success else \
            "reject: " + ", ".join(_failure_reasons(res)[:6])
        math = {"keep_rule": (f"{cfg.mode}: constraints ok AND validation ok AND no protocol violation"
                              + (" AND non-vacuous (lint + discriminative before/after)" if cfg.vacuity_check else "")
                              + (" AND own graded task solved" if cfg.require_task_success else "")),
                "constraints": {"ok": res.constraints.ok, "violations": res.constraints.violations,
                                "warnings": res.constraints.warnings, "blast": res.constraints.blast},
                "protocol_violations": res.protocol_violations,
                "validation": {"ok": val.ok, "n_run": val.n_run, "n_passed": val.n_passed,
                               "n_skipped": val.n_skipped, "blocked": val.blocked,
                               "commands": validation_rows(val.report)},
                "vacuity": res.vacuity.to_dict() if res.vacuity is not None else None,
                "task_score": trial.score, "success_threshold": cfg.success_threshold, "task_solved": solved,
                "new_gene_sample": retry_info,
                "extra_failures": meta.get("extra_failures"), "composite_score": res.score,
                "failure_mode": list(res.failure_mode) if res.failure_mode else None,
                "rolled_back": res.rolled_back,
                "publish_eligibility": {"composite": res.score, "broadcast_score_min": BROADCAST_SCORE,
                                        "publish_score_min": MIN_PUBLISH_SCORE,
                                        "capsule_streak": res.capsule.success_streak if res.capsule else 0,
                                        "streak_min": BROADCAST_STREAK,
                                        "blast_limits": [A2A_MAX_FILES, A2A_MAX_LINES],
                                        "eligible_to_broadcast": res.eligible_to_broadcast,
                                        "publishable": res.publishable}}
        self.tracer.gate(self.t, gene.id if gene is not None else "no_gene", res.success, reason, math=math,
                         stage="solidify", event_id=res.event.id)

    def _skip(self, task: Task, signals: list, dec, trial, tokens: int, reused, qd, hub_hit) -> CycleResult:
        """Safe mode, solved without a gene: a ``skipped`` ledger node only (no event, no memory outcome)."""
        nid = f"skip_{self.name}_{self.t:06d}"
        self.ledger.add(Node(id=nid, parent=self.store.last_event_id(), round=self.t, kind="cycle", status="skipped",
                             score=None, cost=float(tokens), change=f"solved {task.id} without a gene",
                             metrics={"task_score": trial.score, "tokens": tokens},
                             meta={"signals": signals[:12], "reused": reused, "quarantine": qd}))
        self._last_trace = (trial.trace or "")[-4000:]
        cr = CycleResult(self.t, task.id, task.family, signals, None, "none", reused, trial.score, True, False, 0.0,
                         int(tokens), self.proposer_calls, hub_hit, qd, None, nid, dec.mode, sorted(dec.banned))
        self.results.append(cr)
        return cr

    def _auto_gene(self, signals: list) -> Gene:
        """Faithful: ``gene_auto_<hash>`` whose ``git diff --check`` validation the allowlist blocks."""
        gid = "gene_auto_" + hashlib.sha256(signal_key(signals).encode()).hexdigest()[:8]
        g = self.store.genes.get(gid)
        if g is None:
            g = Gene(id=gid, category="repair" if "log_error" in signals else "optimize",
                     signals_match=list(signals[:8]),
                     strategy=["Identify the problem from the signals.", "Apply a minimal change.",
                               "Validate.", "Record the outcome.", "Roll back on failure.", "Summarize."],
                     constraints={"max_files": 12, "forbidden_paths": [".git", "node_modules"]},
                     validation=["git diff --check"], provenance={"kind": "auto"})
            self.store.upsert_gene(g)
        return g

    def make_bundle(self, gene: Gene, capsule, event, report: Optional[dict], before: dict, after: dict) -> Bundle:
        g = gene.copy()
        g.learning_history, g.anti_patterns, g.epigenetic_marks = [], [], []
        g.stamp()
        return Bundle(gene=g.to_dict(), capsule=capsule.to_dict() if capsule is not None else None,
                      event=event.to_dict() if event is not None else None, report=report, pre_state=dict(before),
                      post_state=dict(after), meta={"author": self.name})

    def publish(self, gene: Gene, capsule, event, report, before, after) -> Optional[str]:
        key = gene.content_key()
        if key in self._published:
            return None
        self._published.add(key)
        b = self.behavior.modify_bundle(self.make_bundle(gene, capsule, event, report, before, after), self)
        d: Decision = self.hub.publish(b, self.name)
        tr = self.tracer
        if tr.enabled:
            last = getattr(getattr(self.hub, "bank", None), "last", {}) or {}
            if d.report and last:
                for k, r in last.items():
                    tr.event("eval", self.t, **eval_payload(f"hub-bank:{k}:{gene.id}", r,
                                                           stage="hub verification on its hidden task bank"))
            tr.gate(self.t, f"hub-publish:{gene.id}", d.accepted, f"hub {getattr(self.hub, 'name', '?')}: "
                    f"{d.status}" + (f" - {'; '.join(map(str, d.reasons))}" if d.reasons else ""),
                    math={k: v for k, v in (d.report or {}).items()
                          if k in ("n", "n_off", "k", "U", "U_LCB", "delta", "R", "uplift_ok", "floor_ok", "verdict",
                                   "base_in", "gene_in", "discriminative", "recomputed_blast", "reason")},
                    stage="publish -> hub gate + verification", asset_id=(d.asset_id or "")[:24])
        return d.status
