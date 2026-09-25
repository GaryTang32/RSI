"""SafeHub: verifiable execution, adoption-based ranking, adoption-only credits (spec §9.2).

Asset states::

    submitted -> gate -> quarantined -> verified -> promoted
                                     \\-> rejected
    promoted -> deprecated (no verified adoption for a while) | revoked (failed spot-check / author)

1. **Static gate** (§9.2.2): schema + asset_id; bundle rule and forgery guard
   (non-empty execution trace with integer exit codes, a diff); command policy;
   **vacuity lint**; leakage screen against the hub's own task bank; prompt-
   injection / escalation lint; size ceiling (<= 500 tokens); near-duplicate
   merge into an existing lineage. Self-reported ``confidence``,
   ``success_streak``, ``outcome.score`` are ignored; ``blast_radius`` is
   recomputed from the proof material.
2. **Verified execution** (§9.2.3), run by the hub: (a) discriminative
   validation ``V(W1)=pass and V(W0)=fail`` (+ mutation kill rate); (b)
   task-level uplift on a hidden :class:`TaskBank`: a frozen reference executor
   with and without the gene on n in-scope and n' off-scope tasks, paired seeds,
   k repeats. Accept iff ``U_LCB >= delta`` (:class:`UpliftLCB`, a
   :class:`rsi.core.Gate`) and ``R >= -delta`` (:class:`rsi.core.NoiseFloor`),
   delta from :func:`rsi.core.noise_from_trials` on the baseline runs. Output:
   a hub-signed ValidationReport.
3. **Ranking** (§9.2.4): rank(a) = q_a * (1 - exp(-n_a/nu)) * (0.5 + 0.5 u_a) *
   0.5^(age/h), q_a = 5% quantile of Beta(1+s, 1+f) over counted adoption
   reports; exploration slots (share eps) filled by Thompson sampling among
   verified-but-unadopted assets.
4. **Credits** (§9.2.5): publish stake (refunded at verified, slashed at
   rejected), zero for promotion, credits only per verified adoption by a
   distinct consumer (first success per consumer, per-epoch cap); adoption
   reports count only from non-author, non-cluster consumers with reputation,
   <= 1 per (consumer, asset, epoch); a fraction rho is re-run by the hub and a
   disagreeing reporter is slashed.
"""
from __future__ import annotations

import copy
import math
import random
import re
from dataclasses import dataclass, field
from typing import Optional, Sequence

from rsi.core import (Artifact, Domain, Evaluator, Gate, GateContext, LLM, NoiseFloor, Scored, Verdict,
                      beta_lcb, noise_from_trials, paired_diff_ci)
from rsi.core.llm import estimate_tokens

from .assets import Gene
from .distill import LeakageAuditor
from .hashing import sha256_text
from .hub import AssetRecord, AssetView, Bundle, Decision, _HubBase
from .inject import FileInjector, Injector
from .schema import JsonSchemaValidator
from .signals import RunContext, TaskSignalExtractor, cosine, jaccard, pattern_hits, tokens
from .solidify import CountedFilePolicy, blast_radius
from .validation import CommandPolicy, InProcessExecutor, ValidationRunner, VacuityDetector

INJECTION_PATTERNS = [
    r"ignore (all )?(previous|prior|above) (instructions|rules)", r"disregard (the )?(system|previous)",
    r"(disable|skip|bypass|delete|remove) (the )?(tests?|validation|checks?|safety|guard)",
    r"exfiltrat", r"rm -rf", r"curl .*\|\s*(sh|bash)", r"(print|reveal|send) (the )?(api[_ ]?key|secret|token|password)",
    r"allow_only", r"you are now", r"system prompt",
]


def injection_lint(g: Gene) -> list[str]:
    """Prompt-injection / escalation findings in strategy, avoid, summary and tool policy."""
    text = "\n".join([g.summary, *g.strategy, *g.avoid, *g.preconditions]).lower()
    out = [p for p in INJECTION_PATTERNS if re.search(p, text)]
    tp = g.extra.get("tool_policy") if g.extra else None
    if isinstance(tp, dict) and tp.get("allow_only"):
        out.append("tool_policy.allow_only widening")
    return out


#: agent-internal task-level checks; at the hub a gene validated only by these is a *strategy gene* whose
#: verification is the task-bank uplift test (b), not the workspace before/after run (a)
TASK_CHECKS = ("rsi-taskcheck",)


def is_strategy_gene(g: Gene) -> bool:
    return bool(g.validation) and all(str(v).split()[0] in TASK_CHECKS for v in g.validation if str(v).split())


class UpliftLCB(Gate):
    """Accept iff the paired lower confidence bound of (candidate - incumbent)
    over per-task scores is at least ``ctx.delta`` (one-sided level alpha/2... see ``alpha``)."""

    name = "uplift_lcb"

    def __init__(self, alpha: float = 0.10, reps: int = 2000) -> None:
        self.alpha = alpha           # 0.10 two-sided -> the 5% one-sided lower bound
        self.reps = reps

    def check(self, cand: Scored, inc: Scored, ctx: GateContext) -> Verdict:
        ids = sorted(set(cand.per_task or {}) & set(inc.per_task or {}))
        if len(ids) < 2:
            return Verdict(False, "too few paired tasks", {"n": len(ids)})
        d = paired_diff_ci([inc.per_task[i] for i in ids], [cand.per_task[i] for i in ids], alpha=self.alpha,
                           reps=self.reps, seed=ctx.round)
        # U_LCB >= delta AND U_LCB > 0: with a bank at ceiling (every baseline trial solved) the calibrated delta is
        # 0 and `U_LCB >= delta` alone verified a gene with U = U_LCB = 0 (live validation run, RUNS.md)
        ok = d["lo"] >= ctx.delta and d["lo"] > 0
        why = (f"{'>=' if d['lo'] >= ctx.delta else '<'} delta={ctx.delta:.3f}"
               + ("" if d["lo"] > 0 or d["lo"] < ctx.delta else " but LCB <= 0 (no established uplift)"))
        return Verdict(ok, f"U={d['mean_diff']:+.3f} LCB={d['lo']:+.3f} {why}",
                       {"U": d["mean_diff"], "U_LCB": d["lo"], "n": d["n"]})


@dataclass
class TaskBank:
    """Hub-owned tasks with hidden graders (a sealed split agents never see) and a
    frozen reference executor (harness + model)."""

    domain: Domain
    harness: Artifact
    llm: Optional[LLM]
    split: str = "test"
    injector: Injector = field(default_factory=FileInjector)
    extractor: TaskSignalExtractor = field(default_factory=TaskSignalExtractor)
    n: int = 24
    n_off: int = 12
    k: int = 2
    z: float = 2.0
    delta: Optional[float] = None          # fixed delta; None = calibrated from the baseline trials
    delta_scale: float = 1.0
    workers: int = 1

    def __post_init__(self) -> None:
        self.ev = Evaluator(self.domain, self.llm, workers=self.workers, allow_sealed=True)
        self.tasks = self.domain.tasks.split(self.split, allow_sealed=True)
        self._sig = {t.id: self.extractor.extract(RunContext(task=t)) for t in self.tasks}
        self.last: dict = {}           # the last measure()'s EvalResults (for run traces only; never read back)

    def scope(self, g: Gene) -> tuple[list, list]:
        ins = [t for t in self.tasks if pattern_hits(g.signals_match, self._sig[t.id]) > 0]
        off = [t for t in self.tasks if pattern_hits(g.signals_match, self._sig[t.id]) == 0]
        return ins, off

    def measure(self, g: Gene, rng: random.Random, *, n: Optional[int] = None) -> dict:
        ins, off = self.scope(g)
        n = n or self.n
        ins = rng.sample(ins, min(n, len(ins)))
        off = rng.sample(off, min(self.n_off, len(off)))
        out = {"n": len(ins), "n_off": len(off), "k": self.k, "seeds": list(range(self.k)),
               "task_ids_hash": sha256_text("|".join(sorted(t.id for t in ins + off)))[:16]}
        if len(ins) < 2:
            return {**out, "U": 0.0, "U_LCB": -1.0, "R": 0.0, "delta": 0.0, "reason": "too few in-scope bank tasks"}
        art = self.injector.inject(self.harness, [g])
        base_in = self.ev.evaluate(self.harness, ins, self.k, label="bank")
        gene_in = self.ev.evaluate(art, ins, self.k, label="bank")
        self.last = {"base_in": base_in, "gene_in": gene_in}
        if self.delta is not None:
            delta = self.delta
        else:
            delta = noise_from_trials(base_in.trial_matrix(), z=self.z).delta if self.k >= 2 else 0.05
        delta *= self.delta_scale
        cand, inc = Scored.from_eval(gene_in), Scored.from_eval(base_in)
        v = UpliftLCB().check(cand, inc, GateContext(delta=delta, round=rng.randrange(10 ** 6)))
        R, floor_ok = 0.0, True
        if off:
            base_off = self.ev.evaluate(self.harness, off, self.k, label="bank")
            gene_off = self.ev.evaluate(art, off, self.k, label="bank")
            self.last.update(base_off=base_off, gene_off=gene_off)
            R = gene_off.score - base_off.score
            fv = NoiseFloor().check(Scored(gene_off.score), Scored(base_off.score),
                                    GateContext(best_score=base_off.score, delta=delta))
            floor_ok = fv.accept
        return {**out, "U": v.details.get("U", 0.0), "U_LCB": v.details.get("U_LCB", -1.0), "R": R,
                "delta": delta, "uplift_ok": v.accept, "floor_ok": floor_ok, "accept": v.accept and floor_ok,
                "verdict": v.reason, "base_in": base_in.score, "gene_in": gene_in.score}


class SafeHub(_HubBase):
    name = "safe"

    def __init__(self, bank: TaskBank, *, eps: float = 0.2, n_min: int = 2, nu: float = 3.0, h: float = 15.0,
                 stake: float = 20.0, adoption_credit: float = 30.0, adoption_cap: int = 3, rho: float = 0.2,
                 r_min: float = 10.0, max_tokens: int = 500, dup_jaccard: float = 0.6, dup_cos: float = 0.9,
                 require_discriminative: bool = True, min_kill_rate: float = 0.0, deprecate_after: int = 30,
                 executor=None, seed: int = 0, deny_terms: Sequence[str] = (), ledger=None) -> None:
        super().__init__(ledger=ledger)
        self.bank = bank
        self.eps, self.n_min, self.nu, self.h = eps, n_min, nu, h
        self.stake, self.adoption_credit, self.adoption_cap = stake, adoption_credit, adoption_cap
        self.rho, self.r_min = rho, r_min
        self.max_tokens = max_tokens
        self.dup_jaccard, self.dup_cos = dup_jaccard, dup_cos
        self.require_discriminative = require_discriminative
        self.deprecate_after = deprecate_after
        self.rng = random.Random(seed)
        self.runner = ValidationRunner(CommandPolicy.safe(internal=TASK_CHECKS), executor or InProcessExecutor(),
                                       mode="safe")
        self.vacuity = VacuityDetector(self.runner, min_kill_rate=min_kill_rate)
        self.schema = JsonSchemaValidator(strict=False)
        self.auditor = LeakageAuditor(deny_terms or bank.domain.leakage_terms(bank.split))
        self.reputation: dict[str, float] = {}
        self.cluster: dict[str, str] = {}
        self._reports_epoch: dict = {}
        self._paid: dict = {}
        self.n_spot_checks = 0
        self.n_slashed = 0
        self.counted_policy = CountedFilePolicy()

    # ------------------------------------------------------------------ registry
    def register(self, agent: str, cluster: Optional[str] = None, reputation: float = 50.0) -> None:
        self.credits.register(agent)
        self.reputation.setdefault(agent, reputation)
        self.cluster.setdefault(agent, cluster or agent)

    # ------------------------------------------------------------------ gate
    def static_gate(self, b: Bundle) -> tuple[list[str], Optional[AssetRecord]]:
        r: list[str] = []
        r += self._integrity(b)
        for name, a in (("gene", b.gene), ("capsule", b.capsule), ("event", b.event)):
            if a is not None:
                r += [f"{name} schema: {e}" for e in self.schema.validate(a)[:3]]
        c = b.capsule
        if c is None:
            r.append("bundle rule: a Gene needs a Capsule")
        else:
            trace = c.get("execution_trace") or []
            if not trace or not all(isinstance(s.get("exit"), int) for s in trace):
                r.append("forgery guard: empty trace or missing integer exit codes")
            if not (c.get("diff") or c.get("proof_of_work")):
                r.append("bundle rule: no diff / proof_of_work")
        strategy_gene = is_strategy_gene(Gene.from_dict(b.gene))
        if b.pre_state is None or b.post_state is None:
            if not strategy_gene:
                r.append("no proof material (pre/post workspace) for verified execution")
        elif not strategy_gene:
            br = blast_radius(b.pre_state, b.post_state, self.counted_policy)
            if br["files"] == 0 and br["lines"] == 0:
                r.append("forgery guard: zero recomputed blast radius")
        g = Gene.from_dict(b.gene)
        for cmd in g.validation:
            pc = self.runner.policy.check(cmd)
            if not pc.ok:
                r.append(f"command policy: {cmd!r}: {pc.reason}")
        vac, lint = self.vacuity.lint_vacuous(g.validation, b.post_state or {})
        if vac:
            r.append("vacuity lint: " + ",".join(sorted({k for v in lint.values() for k in v})))
        inj = injection_lint(g)
        if inj:
            r.append("injection lint: " + inj[0])
        leak = self.auditor.audit(g.copy(), redact=False)
        if leak.critic_hits:
            r.append("leakage: hub task-bank terms " + ",".join(leak.critic_hits[:3]))
        if estimate_tokens(g.text()) > self.max_tokens:
            r.append(f"size ceiling: {estimate_tokens(g.text())} > {self.max_tokens} tokens")
        dup = None
        if not r:
            for aid in self.order:
                rec = self.records[aid]
                if rec.status not in ("verified", "promoted", "quarantined"):
                    continue
                og = rec.bundle.gene
                if jaccard(og.get("signals_match", []), g.signals_match) >= self.dup_jaccard and cosine(
                        tokens(" ".join(og.get("strategy", []))), tokens(" ".join(g.strategy))) >= self.dup_cos:
                    dup = rec
                    break
        return r, dup

    # ------------------------------------------------------------------ publish
    def publish(self, bundle: Bundle, author: str) -> Decision:
        self.register(author)
        self.credits.credit(author, -self.stake, "stake", self.epoch)
        aid = bundle.gene.get("asset_id") or "none"
        reasons, dup = self.static_gate(bundle)
        if aid in self.records:
            reasons.append("duplicate asset_id")
        if reasons:
            if aid not in self.records and aid != "none":
                rec = AssetRecord(aid, copy.deepcopy(bundle), author, "rejected", self.epoch, reasons=reasons[:5])
                self.records[aid] = rec
                self.order.append(aid)
            self._node("publish", "rejected", aid, author, stage="gate", reasons=reasons[:5])
            return Decision(False, "rejected", aid, reasons)
        if dup is not None:
            dup.version += 1
            dup.reasons.append(f"merged near-duplicate from {author}")
            self.credits.credit(author, self.stake, "stake_refund", self.epoch)
            self._node("publish", "merged", dup.asset_id, author)
            return Decision(True, "merged", dup.asset_id, ["near-duplicate merged into existing lineage"])
        rec = AssetRecord(aid, copy.deepcopy(bundle), author, "quarantined", self.epoch,
                          lineage=sha256_text(repr(Gene.from_dict(bundle.gene).content_key()))[:16])
        self.records[aid] = rec
        self.order.append(aid)
        self._node("publish", "quarantined", aid, author)
        ok, report = self.verify(rec)
        rec.hub_report = report
        if ok:
            rec.status = "verified"
            self.credits.credit(author, self.stake, "stake_refund", self.epoch)
        else:
            rec.status = "rejected"
            rec.reasons.append(report.get("reason", "verification failed"))
        self._node("verify", rec.status, aid, author, U=report.get("U"), U_LCB=report.get("U_LCB"),
                   delta=report.get("delta"), discriminative=report.get("discriminative"))
        return Decision(ok, rec.status, aid, [] if ok else [report.get("reason", "verification failed")], report)

    def verify(self, rec: AssetRecord) -> tuple[bool, dict]:
        b, g = rec.bundle, rec.gene
        rep: dict = {"type": "ValidationReport", "signer": "hub", "hub_run_id": f"hub_{len(self.ledger):07d}",
                     "executor_id": getattr(self.bank.llm, "name", "reference"), "gene_id": g.id}
        br = blast_radius(b.pre_state or {}, b.post_state or {}, self.counted_policy)
        rep["recomputed_blast"] = {"files": br["files"], "lines": br["lines"]}
        if is_strategy_gene(g):
            rep["discriminative"] = {"skipped": "strategy gene: verified by task-level uplift only"}
            disc = None
        else:
            ws_specs = [v for v in g.validation if str(v).split()[:1] and str(v).split()[0] not in TASK_CHECKS]
            disc = self.vacuity.discriminate(ws_specs, b.pre_state or {}, b.post_state or {})
            rep["discriminative"] = disc.to_dict()
        if disc is not None and self.require_discriminative and not disc.discriminative:
            rep["reason"] = "validation not discriminative (passes before the change or fails after it)"
            return False, rep
        if disc is not None and disc.kill_rate is not None and disc.kill_rate < self.vacuity.min_kill_rate:
            rep["reason"] = f"mutation kill rate {disc.kill_rate:.2f} too low"
            return False, rep
        m = self.bank.measure(g, self.rng)
        rep.update({k: m[k] for k in ("n", "n_off", "k", "seeds", "task_ids_hash", "U", "U_LCB", "R", "delta",
                                       "uplift_ok", "floor_ok", "verdict", "base_in", "gene_in")
                    if k in m})
        if not m.get("accept"):
            rep["reason"] = m.get("reason") or f"uplift not established: {m.get('verdict', '')}; R={m.get('R', 0):+.3f}"
            return False, rep
        rep["reason"] = "verified"
        return True, rep

    # ------------------------------------------------------------------ search / ranking
    def u_of(self, rec: AssetRecord) -> float:
        return float(min(1.0, max(0.0, (rec.hub_report or {}).get("U_LCB", 0.0))))

    def rank_score(self, rec: AssetRecord) -> float:
        s = sum(1 for a in rec.adoptions if a.get("counted") and a["outcome"] == 1)
        f = sum(1 for a in rec.adoptions if a.get("counted") and a["outcome"] == 0)
        n_a = len(rec.adopters(counted_only=True))
        q = beta_lcb(s, f, q=0.05)
        last = max([a["epoch"] for a in rec.adoptions if a.get("counted") and a["outcome"] == 1],
                   default=rec.epoch)
        return q * (1 - math.exp(-n_a / self.nu)) * (0.5 + 0.5 * self.u_of(rec)) * 0.5 ** ((self.epoch - last) / self.h)

    def search(self, signals: Sequence[str], k: int = 5, consumer: Optional[str] = None) -> list[AssetView]:
        cands = self._matching(signals, ("verified", "promoted"))
        cands = [(r, sim) for r, sim in cands if r.author != consumer]
        adopted = [(r, sim) for r, sim in cands if r.adopters(True)]
        fresh = [(r, sim) for r, sim in cands if not r.adopters(True)]
        exploit = sorted(((self.rank_score(r), r, sim) for r, sim in adopted), key=lambda x: (-x[0], x[1].asset_id))
        views = [AssetView(r.asset_id, copy.deepcopy(r.bundle.gene), copy.deepcopy(r.bundle.capsule), sc, r.status,
                           r.author, sim) for sc, r, sim in exploit[:k]]
        explore: list[AssetView] = []
        if fresh:
            def ts(rs):
                s = sum(1 for a in rs[0].adoptions if a["outcome"] == 1 and a.get("counted"))
                f = sum(1 for a in rs[0].adoptions if a["outcome"] == 0 and a.get("counted"))
                return self.rng.betavariate(1 + s, 1 + f)
            order = sorted(fresh, key=lambda rs: -ts(rs)) if self.eps > 0 else sorted(
                fresh, key=lambda rs: (-self.u_of(rs[0]), rs[0].asset_id))
            explore = [AssetView(r.asset_id, copy.deepcopy(r.bundle.gene), copy.deepcopy(r.bundle.capsule), 0.0,
                                 r.status, r.author, sim, exploration=True) for r, sim in order[:k]]
        if self.eps > 0 and explore and (not views or self.rng.random() < self.eps):
            out = [explore[0]] + views[: k - 1] + explore[1:]
        else:
            out = views + explore       # without exploration, fresh assets only fill empty slots
        return out[:k]

    def rank(self, signal_class: Optional[str] = None) -> list[tuple[str, float]]:
        recs = [self.records[a] for a in self.order if self.records[a].status in ("verified", "promoted")]
        if signal_class:
            recs = [r for r in recs if pattern_hits(r.bundle.gene.get("signals_match", []), [signal_class]) > 0]
        return sorted(((r.asset_id, self.rank_score(r)) for r in recs), key=lambda x: -x[1])

    # ------------------------------------------------------------------ adoption reports
    def report_outcome(self, asset_id: str, consumer: str, outcome: int, proof: Optional[dict] = None) -> dict:
        self.register(consumer)
        rec = self.records.get(asset_id)
        if rec is None or rec.status not in ("verified", "promoted"):
            return {"counted": False, "reason": "asset not verified"}
        row = {"consumer": consumer, "outcome": int(outcome), "epoch": self.epoch, "counted": False,
               "uplift": (proof or {}).get("dS"), "reason": ""}
        if consumer == rec.author:
            row["reason"] = "self-report by author"
        elif self.cluster.get(consumer) == self.cluster.get(rec.author):
            row["reason"] = "same cluster as author"
        elif self.reputation.get(consumer, 0) < self.r_min:
            row["reason"] = "reporter reputation below r_min"
        elif self._reports_epoch.get((consumer, asset_id, self.epoch), 0) >= 1:
            row["reason"] = "report cap per (consumer, asset, epoch)"
        elif not proof or "tasks_hash" not in proof:
            row["reason"] = "no verifiable proof"
        else:
            self._reports_epoch[(consumer, asset_id, self.epoch)] = 1
            row["counted"] = True
            if self.rng.random() < self.rho:            # hub re-runs a random fraction of reports
                self.n_spot_checks += 1
                m = self.bank.measure(rec.gene, self.rng)
                disagree = (outcome == 1 and m["U"] <= 0) or (outcome == 0 and m["U_LCB"] >= m["delta"] > 0)
                row["spot_check"] = {"U": m["U"], "U_LCB": m["U_LCB"], "agree": not disagree}
                if disagree:
                    row["counted"] = False
                    row["reason"] = "spot-check disagreement: reporter slashed"
                    self.reputation[consumer] = self.reputation.get(consumer, 50) - 20
                    self.credits.credit(consumer, -self.stake, "slash", self.epoch)
                    self.n_slashed += 1
        rec.adoptions.append(row)
        if row["counted"] and outcome == 1:
            key = (asset_id, consumer)
            paid_now = sum(1 for (a, _), e in self._paid.items() if a == asset_id and e == self.epoch)
            if key not in self._paid and paid_now < self.adoption_cap:
                self._paid[key] = self.epoch
                self.credits.credit(rec.author, self.adoption_credit, "verified_adoption", self.epoch)
            succ = {a["consumer"] for a in rec.adoptions if a.get("counted") and a["outcome"] == 1}
            if rec.status == "verified" and len(succ) >= self.n_min:
                rec.status = "promoted"
                rec.promoted_epoch = self.epoch
        self._node("adopt", "counted" if row["counted"] else "ignored", asset_id, consumer, outcome=int(outcome),
                   reason=row["reason"])
        return row

    def revoke(self, asset_id: str, by: str = "hub", reason: str = "") -> None:
        rec = self.records.get(asset_id)
        if rec and (by == "hub" or by == rec.author):
            rec.status = "revoked"
            rec.reasons.append(f"revoked by {by}: {reason}")
            self._node("revoke", "revoked", asset_id, by, reason=reason)

    def advance_epoch(self) -> None:
        super().advance_epoch()
        for aid in self.order:
            rec = self.records[aid]
            if rec.status == "promoted":
                last = max([a["epoch"] for a in rec.adoptions if a.get("counted") and a["outcome"] == 1],
                           default=rec.epoch)
                if self.epoch - last > self.deprecate_after:
                    rec.status = "deprecated"

    def metrics(self) -> dict:
        from .metrics import ReuseMetrics
        return ReuseMetrics().compute(self)
