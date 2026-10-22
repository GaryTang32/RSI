"""Consumer-side quarantine: lint a fetched gene, then A/B it on the consumer's own held-out tasks (spec §9.2.6)."""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Optional, Sequence

from rsi.core import (Artifact, Domain, Evaluator, GateContext, LLM, RRSIGate, Scored, fixed_noise,
                      noise_from_trials, paired_diff_ci)

from .assets import Gene
from .hashing import sha256_text
from .inject import FileInjector, Injector
from .safehub import injection_lint
from .signals import RunContext, TaskSignalExtractor, pattern_hits
from .validation import CommandPolicy


@dataclass
class QuarantineResult:
    promote: bool
    reason: str
    dS: float = 0.0
    delta: float = 0.0
    n: int = 0
    S_base: float = 0.0
    S_gene: float = 0.0
    proof: dict = field(default_factory=dict)


class QuarantineGate:
    """Promote iff the A/B passes ``gate``: ``rrsi`` (RRSIGate noise floor and cost rule, and dS > 0) or ``lcb``
    (paired lower confidence bound > 0). The proof (task hash, dS, delta, n) goes with the adoption report."""

    def __init__(self, domain: Domain, harness: Artifact, llm: Optional[LLM], tasks: Sequence, *,
                 injector: Optional[Injector] = None, extractor: Optional[TaskSignalExtractor] = None, k: int = 2,
                 n_max: int = 12, min_tasks: int = 3, gate: str = "rrsi", delta: Optional[float] = None,
                 z: float = 2.0, policy: Optional[CommandPolicy] = None, seed: int = 0) -> None:
        self.domain, self.harness, self.llm = domain, harness, llm
        self.tasks = list(tasks)
        self.injector = injector or FileInjector()
        self.extractor = extractor or TaskSignalExtractor()
        self.k, self.n_max, self.min_tasks = k, n_max, min_tasks
        self.gate = gate
        self.delta = delta
        self.z = z
        self.policy = policy or CommandPolicy.safe()
        self.ev = Evaluator(domain, llm, workers=1, allow_sealed=True)
        self.rng = random.Random(seed)
        self._sig = {t.id: self.extractor.extract(RunContext(task=t)) for t in self.tasks}
        self.last: dict = {}           # the last A/B's EvalResults, for traces only

    def test(self, g: Gene) -> QuarantineResult:
        self.last = {}
        inj = injection_lint(g)
        if inj:
            return QuarantineResult(False, f"injection lint: {inj[0]}")
        bad = [c for c in g.validation if not self.policy.check(c).ok]
        if bad:
            return QuarantineResult(False, f"command policy: {bad[0]!r}")
        scope = [t for t in self.tasks if pattern_hits(g.signals_match, self._sig[t.id]) > 0]
        if len(scope) < self.min_tasks:
            return QuarantineResult(False, f"only {len(scope)} in-scope held-out tasks (< {self.min_tasks})")
        scope = self.rng.sample(scope, min(self.n_max, len(scope)))
        base = self.ev.evaluate(self.harness, scope, self.k, label="quarantine")
        cand = self.ev.evaluate(self.injector.inject(self.harness, [g]), scope, self.k, label="quarantine")
        self.last = {"base": base, "cand": cand}
        if self.delta is not None:
            noise = fixed_noise(self.delta)
        elif self.k >= 2:
            noise = noise_from_trials(base.trial_matrix(), z=self.z)
        else:
            noise = fixed_noise(0.05)
        dS = cand.score - base.score
        # S_base lets the hub tell a no-headroom report (baseline at ceiling) from a real "no gain"
        proof = {"tasks_hash": sha256_text("|".join(sorted(t.id for t in scope)))[:16], "dS": dS,
                 "delta": noise.delta, "n": len(scope), "k": self.k, "S_base": base.score, "S_gene": cand.score}
        res = QuarantineResult(False, "", dS, noise.delta, len(scope), base.score, cand.score, proof)
        if self.gate == "lcb":
            ids = sorted(base.task_scores())
            d = paired_diff_ci([base.task_scores()[i] for i in ids], [cand.task_scores()[i] for i in ids], alpha=0.10)
            res.promote = d["lo"] > 0
            res.reason = f"paired LCB {d['lo']:+.3f} {'>' if res.promote else '<='} 0"
            return res
        v = RRSIGate().check(Scored.from_eval(cand), Scored.from_eval(base),
                             GateContext(best_score=base.score, delta=noise.delta))
        res.promote = bool(v.accept) and dS > 0
        res.reason = v.reason if not v.accept else (f"admissible and dS={dS:+.3f} > 0" if dS > 0 else
                                                    f"admissible but dS={dS:+.3f} <= 0 (incumbent kept)")
        return res
