"""AgentNode: one agent's evolution cycle on any rsi.core Domain (spec §3.1, §9.1).

``cycle(task)`` runs, in order:

1. **signals** - task-descriptor signals (+ §4.6 control signals from the event
   history); in faithful timing the *previous* cycle's outcome is written to the
   memory graph here (§4.11);
2. **look locally** - :class:`GeneSelector` over the LocalStore with memory
   advice, bans and drift;
3. **ask the hub** (optional; when nothing local fits, or always in faithful
   mode) - naive consumers take the best hit whose client reuse score clears
   0.72 / 0.55 and inject it directly (``reference`` / ``direct``); safe consumers
   stage it in quarantine and A/B-test it on their own held-out tasks first;
4. **solve** - run the frozen harness with the chosen gene injected; if nothing
   fits, solve from scratch and, when that fails, let the proposer LLM write a
   new gene from the public trace (then retry with it);
5. **solidify** - :class:`Solidifier` (constraints, validation, composite score,
   event + capsule, rollback, gene learning);
6. **distil** after successful solidifies; **publish** eligible bundles.

Every cycle becomes an :class:`rsi.core.Node` in the agent's Ledger (parent =
previous event), LLM usage is metered per role (``task``, ``proposer``,
``distiller``) on the agents' LLM objects.
"""
from __future__ import annotations

import random
import zlib
from dataclasses import dataclass, field
from typing import Optional

from rsi.core import Artifact, Domain, Execution, Ledger, LLM, Node, Task, paired_diff_ci

from .assets import Gene, PersonalityState
from .config import Config
from .distill import Distiller, LeakageAuditor
from .hub import Bundle, Decision, client_reuse_score, reuse_threshold
from .inject import FileInjector, Injector, post_workspace, pre_workspace, task_text
from .memory import Advice, Outcome, OutcomeInferrer
from .mutation import MutationBuilder, PersonalityModel, StrategyPolicy
from .prompts import GENE_WRITER_SYSTEM, gene_writer_prompt, parse_gene
from .quarantine import QuarantineGate
from .selector import GeneScorer, GeneSelector
from .signals import (PlateauDetector, PlateauOverride, RunContext, SignalDeduper, TaskSignalExtractor, has_error,
                      signal_key)
from .solidify import ConstraintChecker, CountedFilePolicy, RunState, Solidifier
from .store import LocalStore
from .validation import CommandPolicy, SubprocessExecutor, ValidationRunner, VacuityDetector


@dataclass
class CycleResult:
    cycle: int
    task_id: str
    family: str
    signals: list
    gene_id: Optional[str]
    source: str                      # local | hub | generated | none
    reused_asset_id: Optional[str]
    task_score: float                # graded on the agent's own task (hidden grader of the domain)
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

    def to_json(self) -> dict:
        return dict(self.__dict__)


class Behavior:
    """Publishing behaviour hooks (honest by default; see ``rsi.evomap.population``)."""

    kind = "honest"
    publishes = True

    def modify_bundle(self, bundle: Bundle, agent: "AgentNode") -> Bundle:
        return bundle


class AgentNode:
    def __init__(self, name: str, domain: Domain, harness: Artifact, *, llm_task: Optional[LLM],
                 llm_propose: Optional[LLM] = None, config: Optional[Config] = None,
                 store: Optional[LocalStore] = None, hub=None, injector: Optional[Injector] = None,
                 extractor: Optional[TaskSignalExtractor] = None, ledger: Optional[Ledger] = None,
                 behavior: Optional[Behavior] = None, heldout_tasks: Optional[list] = None,
                 decision_tasks: Optional[list] = None, executor=None, counted: Optional[CountedFilePolicy] = None,
                 cluster: Optional[str] = None) -> None:
        self.name = name
        self.domain, self.harness = domain, harness
        self.llm_task, self.llm_propose = llm_task, llm_propose
        self.cfg = (config or Config()).resolved()
        cfg = self.cfg
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
                                     rollback=cfg.rollback)
        self.selector = GeneSelector(GeneScorer(cfg.selector_mode, require_match=cfg.require_match),
                                     use_memory=cfg.use_memory)
        self.deduper = SignalDeduper()
        self.plateau = PlateauDetector()
        self.policy = StrategyPolicy(cfg.strategy_preset)
        self.mutations = MutationBuilder()
        self.personality = PersonalityModel(PersonalityState())
        self.inferrer = OutcomeInferrer(cfg.outcome_source)
        # validate_synth filters distilled validations through the mode's allowlist (Evolver's
        # isValidationCommandAllowed; an EMPTY result is still accepted)
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
        if hub is not None and hasattr(hub, "register"):
            hub.register(name, cluster=self.cluster)
        elif hub is not None:
            hub.credits.register(name)
        self.t = 0
        self.results: list[CycleResult] = []
        self._pending: Optional[dict] = None
        self._last_trace = ""
        self._published: set = set()
        self._reviewed: set = set()        # naive consumers review each hub asset once (hub_review_history)
        self._rejected: set = set()        # safe consumers never re-test an asset their quarantine rejected
        self.last_gene: Optional[Gene] = None   # the gene actually used in the last cycle (stored or not)
        self._rollouts = 0
        self.proposer_calls = 0
        self.n_quarantined = 0
        self.n_quarantine_rejected = 0

    # ------------------------------------------------------------------ helpers
    def _split(self, name: Optional[str]) -> list:
        """Tasks of a DECISION split (the agent practises on ``split`` and re-tests hub assets on
        ``heldout_split``). A sealed split (holdout / ood / test) raises SealedSplitError: it must never
        influence the agent's keep or adoption decisions."""
        if not name or name not in self.domain.tasks.splits:
            return []
        return self.domain.tasks.split(name)

    def _run(self, task: Task, genes: list, seed: int):
        art = self.injector.inject(self.harness, genes) if genes else self.harness
        self._rollouts += 1
        return self.domain.run(art, task, seed=seed, llm=self.llm_task)

    @property
    def n_rollouts(self) -> int:
        """Fresh task rollouts this agent paid for: solves, rsi-taskcheck A/Bs and quarantine re-tests."""
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
        """``rsi-taskcheck [--n N]``: the gene vs no gene on the agent's own in-scope decision tasks
        (paired seeds, k trials each); exit 0 iff the paired bootstrap lower bound of the per-task
        improvement is > 0 (one-sided level ``1 - taskcheck_alpha/2``). Discriminative by construction."""
        gene = (ctx or {}).get("gene")
        if gene is None:
            return 1, "no gene in context"
        n = self.cfg.taskcheck_n
        if "--n" in argv:
            try:
                n = int(argv[argv.index("--n") + 1])
            except (ValueError, IndexError):
                pass
        from .signals import pattern_hits
        scope = [t for t in self.decision_tasks
                 if pattern_hits(gene.signals_match, self.extractor.extract(RunContext(task=t))) > 0]
        if not scope:
            return 1, "no in-scope decision tasks"
        scope = scope[:n]
        k = self.cfg.taskcheck_k
        with_g = [sum(self._run(t, [gene], self.seed_base + s).score for s in range(k)) / k for t in scope]
        without = [sum(self._run(t, [], self.seed_base + s).score for s in range(k)) / k for t in scope]
        d = paired_diff_ci(without, with_g, alpha=self.cfg.taskcheck_alpha, reps=1000) if len(scope) > 1 else \
            {"mean_diff": with_g[0] - without[0], "lo": with_g[0] - without[0]}
        ok = d["mean_diff"] > 0 and d["lo"] > 0
        return (0 if ok else 1), (f"taskcheck n={len(scope)} k={k} with={sum(with_g) / len(with_g):.3f} "
                                  f"without={sum(without) / len(without):.3f} paired LCB={d['lo']:+.3f}")

    def _record_pending(self, cur_error: bool) -> None:
        p = self._pending
        self._pending = None
        oc = self.inferrer.infer(prev_error=p["had_error"], cur_error=cur_error, transcript=p["transcript"],
                                 measured=p["measured"], recent_success=self._recent_success())
        self.store.memory.record_outcome(signals=p["signals"], gene_id=p["gene_id"], status=oc.status, score=oc.score,
                                         note=oc.note, observed=oc.observed, predictive=oc.predictive)

    def flush(self) -> None:
        """Write a pending (next-cycle) outcome now (end of a run)."""
        if self._pending is not None:
            self._record_pending(cur_error=False)

    # ------------------------------------------------------------------ hub
    def _consult_hub(self, signals: list, task: Task):
        cfg, hub = self.cfg, self.hub
        views = hub.search(signals, k=cfg.hub_k, consumer=self.name)
        views = [v for v in views if v.author != self.name]
        if not views:
            return None, None, None, False
        if cfg.reuse_mode in ("reference", "direct"):
            thr = reuse_threshold(signals, cfg.reuse_threshold, cfg.reuse_threshold_problem)
            best = max(views, key=lambda v: (client_reuse_score(v), v.score))
            if client_reuse_score(best) < thr:
                return None, None, None, False
            b = hub.fetch(best.asset_id, self.name)
            if b is None:
                return None, None, None, False
            return Gene.from_dict(b.gene), best.asset_id, None, True
        if cfg.reject_memory:
            views = [v for v in views if v.asset_id not in self._rejected]
            if not views:
                return None, None, None, False
        v = views[0]
        b = hub.fetch(v.asset_id, self.name)
        if b is None:
            return None, None, None, False
        try:
            self.store.stage_external(b.gene, source=hub.name, hub_report_id=(v.asset_id or "")[:24])
        except ValueError:
            self._rejected.add(v.asset_id)
            return None, v.asset_id, {"promote": False, "reason": "asset_id integrity check failed"}, True
        self.n_quarantined += 1
        q = self.quarantine.test(Gene.from_dict(b.gene))
        qd = {"promote": q.promote, "reason": q.reason, "dS": q.dS, "delta": q.delta, "n": q.n}
        if q.promote:
            g = self.store.promote_external(v.asset_id, validated=True)
            hub.report_outcome(v.asset_id, self.name, 1, q.proof)
            return g, v.asset_id, qd, True
        self.n_quarantine_rejected += 1
        self._rejected.add(v.asset_id)
        self.store.resolve_external(v.asset_id, "rejected", q.reason)
        if q.n > 0:
            hub.report_outcome(v.asset_id, self.name, 0, q.proof)
        return None, v.asset_id, qd, True

    # ------------------------------------------------------------------ propose
    def _propose(self, task: Task, signals: list, trial) -> Optional[Gene]:
        if self.llm_propose is None:
            return None
        pub = ""
        hook = getattr(self.domain, "public_feedback", None)
        if callable(hook):
            pub = str(hook(task, Execution(output=trial.output, trace=trial.trace, meta=trial.meta or {})))
        trace = (trial.trace or "")[-1500:] + ("\nPublic checks:\n" + pub if pub else "") + \
            f"\nResult: {'solved' if trial.score >= self.cfg.success_threshold else 'NOT solved'}"
        prompt = gene_writer_prompt(signals, task_text(self.domain, task), trace, validation_hint=self.cfg.validation_hint)
        self.proposer_calls += 1
        resp = self.llm_propose.complete(prompt, system=GENE_WRITER_SYSTEM, seed=self.seed_base + self.t,
                                         role="proposer")
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
        self.auditor.audit(g, public_text=task_text(self.domain, task),
                           hidden_text=f"{task.target}\n{trial.feedback}")
        return g

    # ------------------------------------------------------------------ the cycle
    def cycle(self, task: Task, seed: Optional[int] = None) -> CycleResult:
        cfg, st = self.cfg, self.store
        self.t += 1
        st.clock.tick()
        seed = self.seed_base + self.t if seed is None else seed
        recent = st.recent_events(80)
        # faithful: the corpus is the recent session log (the previous attempt's trace), as in Evolver
        corpus = self._last_trace if cfg.carry_log_signals else ""
        base_sig = self.extractor.extract(RunContext(task=task, corpus=corpus))
        if self._pending is not None:
            self._record_pending(cur_error=has_error(base_sig))
        dd = self.deduper.apply(base_sig, recent)
        signals = dd.signals
        st.memory.record("signal", signal={"key": signal_key(signals), "signals": list(signals),
                                           "error_signature": next((x for x in signals if x.startswith("errsig:")),
                                                                   None)})
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
        if self.hub is not None and (cfg.hub_when == "always" or gene is None):
            hg, aid, qd, hub_hit = self._consult_hub(signals, task)
            if hg is not None:
                gene, source, reused = hg, "hub", aid
        pol = self.policy.adaptive(recent, gene, signals, self.t)
        pers = self.personality.select_for_run(drift, signals, self._recent_success())
        # §3.4: innovate mode also when creativity >= 0.75 and the last 6 outcomes are all successes with mean
        # score >= 0.7 (which adds stable_success_plateau); high risk only for a known, rigorous, cautious persona
        last6 = [e.outcome for e in st.recent_events(6)]
        creative = (pers.creativity >= 0.75 and len(last6) == 6 and all(o.get("status") == "success" for o in last6)
                    and sum(float(o.get("score", 0.0)) for o in last6) / 6 >= 0.7)
        if creative and "stable_success_plateau" not in signals:
            signals = signals + ["stable_success_plateau"]
        allow_high = (drift and self.personality.known and pers.rigor >= 0.8 and pers.risk_tolerance <= 0.3
                      and "log_error" not in signals)
        # buildMutation resolves the preset WITHOUT signals (only the cycle-count rule applies there)
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
        ptok0 = self.llm_propose.meter.total().total_tokens if self.llm_propose is not None else 0
        tokens = 0
        if gene is not None:
            trial = self._run(task, [gene], seed)
        else:
            trial = self._run(task, [], seed)
            if cfg.propose and trial.score < cfg.success_threshold:
                g_new = self._propose(task, signals, trial)
                if g_new is not None:
                    gene, source = g_new, "generated"
                    if cfg.retry_after_propose:
                        tokens += trial.tokens
                        trial = self._run(task, [gene], seed + 7919)
        tokens += trial.tokens
        if self.llm_propose is not None:
            tokens += self.llm_propose.meter.total().total_tokens - ptok0
        solved = trial.score >= cfg.success_threshold
        self.last_gene = gene
        if gene is None and solved and cfg.skip_geneless_success:
            return self._skip(task, signals, dec, trial, tokens, reused, qd, hub_hit)
        if gene is None and cfg.mode == "faithful":
            gene = self._auto_gene(signals)
            source = "auto"
        ex = Execution(output=trial.output, trace=trial.trace, meta=trial.meta or {}, error=trial.error)
        before = pre_workspace(self.domain, task)
        after = post_workspace(self.domain, task, ex) if trial.error is None else dict(before)
        is_new = gene is not None and gene.id not in st.genes
        rs = RunState(run_id=f"run_{self.name}_{self.t}", signals=signals, gene=gene, mutation=mut, personality=pers,
                      before=before, after=after, capsule=dec.capsule, estimate=pol.blast_estimate,
                      source_type={"hub": "reused" if cfg.reuse_mode != "reference" else "reference"}.get(
                          source, "generated"),
                      reused_asset_id=reused, personality_known=self.personality.known, task_id=task.id,
                      task_success=solved, hidden_score=trial.score, env=cfg.env,
                      validation_context={"gene": gene, "task": task})
        res = self.solidifier.solidify(rs)
        if res.success and is_new:
            if source == "hub" and reused:
                gene.parent = reused
            st.upsert_gene(gene)
            self.ledger.add(Node(id=f"gene:{gene.id}", parent=None, round=self.t, kind="gene", status="accepted",
                                 change=f"{source} gene {gene.id}", artifact_id=gene.asset_id,
                                 meta={"source": source, "reused_asset_id": reused}))
        # memory-graph outcome
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
        # naive consumers review the reused asset (self-assessed outcome)
        if self.hub is not None and reused and cfg.reuse_mode in ("reference", "direct") \
                and reused not in self._reviewed:            # §4.17: one review per asset
            self._reviewed.add(reused)
            self.hub.report_outcome(reused, self.name, int(res.success),
                                    {"score": res.score, "violations": res.constraints.violations})
        distilled = None
        if res.success and cfg.distill:
            dr = self.distiller.maybe_distill(st, llm=self.llm_propose if cfg.llm_distill else None,
                                              failures=bool(cfg.failure_distill))
            if dr is not None and dr.ok and dr.gene is not None:
                distilled = dr.gene.id
            fr = self.distiller.last_failure_result
            if fr is not None and fr.ok and fr.gene is not None:
                distilled = f"{distilled},{fr.gene.id}" if distilled else fr.gene.id
        published = None
        if self.hub is not None and cfg.publish and self.behavior.publishes and res.success and gene is not None \
                and rs.source_type != "reused" and (res.publishable or not cfg.publish_requires_eligibility):
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
        cr = CycleResult(self.t, task.id, task.family, signals, gene.id if gene else None, source, reused,
                         trial.score, solved, res.success, res.score, int(tokens), self.proposer_calls, hub_hit, qd,
                         published, rid, dec.mode, sorted(dec.banned), distilled, res.validation.ok,
                         res.validation.n_run)
        self.results.append(cr)
        return cr

    def _skip(self, task: Task, signals: list, dec, trial, tokens: int, reused, qd, hub_hit) -> CycleResult:
        """Safe mode: solved without any gene -> nothing to solidify; logged as a ``skipped`` ledger node
        (no EvolutionEvent, no memory-graph outcome, no plateau/failure signal)."""
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
        """Faithful: solidify without a selected gene creates ``gene_auto_<hash>`` whose validation
        (``git diff --check``) is BLOCKED by the allowlist, so the cycle fails validation."""
        import hashlib
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

    # ------------------------------------------------------------------ publish
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
        return d.status
