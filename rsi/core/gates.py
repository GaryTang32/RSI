"""Keep-or-discard rules ("gates").

Every technique ends its loop with a keep rule, and most of what separates them is
this rule:

=====================  =========================================================
autoresearch           :class:`StrictImprovement` (strictly better on one number)
autoresearch-mlx port  :class:`BootstrapRigor` (P(better) >= 0.95 over seeds)
RRSI                   :class:`RRSIGate` = noise floor + cost rule + within-band
                       rule + domain guards, then argmax (:func:`select`)
GEPA                   :class:`StrictImprovement` on a minibatch
SoL-Pi                 :class:`DualGate` (capability within tolerance AND some
                       efficiency metric improves)
EvoMap (naive)         self-reported validation - see ``rsi.evomap``
=====================  =========================================================

Gates are composable (:class:`AllOf`) and are pure functions of the scores, so
they can be re-adjudicated later with different parameters (RRSI's
``readjudicate``).

All threshold comparisons treat differences smaller than :data:`TIE_EPS` as
ties. Scores are float means, and two candidates with the same per-task scores
can differ in the last bit depending on summation order; without the tolerance a
mathematically tied candidate would pass a "strictly better" rule at random.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Optional, Sequence

from .evaluate import EvalResult
from .stats import prob_better_bootstrap

#: Differences below this are float noise, not gains or losses.
TIE_EPS = 1e-9


@dataclass
class Scored:
    """What a gate sees about one candidate (or the incumbent)."""

    score: float
    cost: float = 0.0                           # e.g. mean tokens per trial
    samples: Optional[list[float]] = None       # per-seed / per-run scores (for bootstrap rules)
    per_task: Optional[dict[str, float]] = None
    metrics: dict[str, float] = field(default_factory=dict)  # extra metrics (valid_rate, tokens, steps ...)
    novelty: int = 0                            # RRSI nu: new structural components touched

    @classmethod
    def from_eval(cls, ev: EvalResult, **extra) -> "Scored":
        m = ev.trial_matrix()
        samples = list(m.mean(axis=0)) if m.size else [ev.score]
        metrics = {"score": ev.score, "cost": ev.cost, "steps": ev.steps, "error_rate": ev.error_rate}
        metrics.update(extra.pop("metrics", {}))
        return cls(score=ev.score, cost=ev.cost, samples=samples, per_task=ev.task_scores(), metrics=metrics, **extra)


@dataclass
class GateContext:
    best_score: Optional[float] = None       # S*: best incumbent score so far (running max)
    delta: float = 0.0                       # noise band
    round: int = 0
    info: dict = field(default_factory=dict)


@dataclass
class Verdict:
    accept: bool
    reason: str
    details: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.accept = bool(self.accept)   # numpy scores give numpy.bool_, which __bool__ may not return

    def __bool__(self) -> bool:
        return self.accept


class Gate:
    name = "gate"

    def check(self, cand: Scored, inc: Scored, ctx: GateContext) -> Verdict:  # pragma: no cover
        raise NotImplementedError

    def __and__(self, other: "Gate") -> "AllOf":
        return AllOf([self, other])


class AllOf(Gate):
    name = "all_of"

    def __init__(self, gates: Sequence[Gate]) -> None:
        self.gates = []
        for g in gates:
            self.gates.extend(g.gates if isinstance(g, AllOf) else [g])

    def check(self, cand, inc, ctx):
        details = {}
        for g in self.gates:
            v = g.check(cand, inc, ctx)
            details[g.name] = v.reason
            if not v.accept:
                return Verdict(False, f"{g.name}: {v.reason}", details)
        return Verdict(True, "all gates passed", details)


class StrictImprovement(Gate):
    """Keep iff strictly better than the incumbent (autoresearch, GEPA minibatch)."""

    name = "strict_improvement"

    def __init__(self, lower_is_better: bool = False, min_gain: float = 0.0) -> None:
        self.lower = lower_is_better
        self.min_gain = min_gain

    def check(self, cand, inc, ctx):
        gain = (inc.score - cand.score) if self.lower else (cand.score - inc.score)
        ok = gain > self.min_gain + TIE_EPS
        return Verdict(ok, f"gain {gain:+.4f} {'>' if ok else '<='} {self.min_gain}", {"gain": gain})


class ImprovementOrEqual(Gate):
    name = "improvement_or_equal"

    def check(self, cand, inc, ctx):
        ok = cand.score >= inc.score - TIE_EPS
        return Verdict(ok, f"{cand.score:.4f} {'>=' if ok else '<'} {inc.score:.4f}")


class MinGain(Gate):
    """Keep iff the gain exceeds the noise band ("ignore wins smaller than the noise")."""

    name = "min_gain"

    def check(self, cand, inc, ctx):
        gain = cand.score - inc.score
        ok = gain > ctx.delta + TIE_EPS
        return Verdict(ok, f"gain {gain:+.4f} vs delta {ctx.delta:.4f}", {"gain": gain})


class NoiseFloor(Gate):
    """RRSI floor: S' >= S* - delta, with S* the best incumbent score so far.
    Blocks a slow slide made of many small, individually-within-noise losses."""

    name = "noise_floor"

    def check(self, cand, inc, ctx):
        ref = ctx.best_score if ctx.best_score is not None else inc.score
        floor = ref - ctx.delta
        ok = cand.score >= floor - TIE_EPS
        return Verdict(ok, f"S'={cand.score:.4f} {'>=' if ok else '<'} S*-delta={floor:.4f}", {"floor": floor})


class CostRule(Gate):
    """RRSI cost rule on relative cost dC = (C' - C) / C.

    * gain above noise (dS > delta):  dC <= beta0 + beta1 * dS
    * gain within the band (dS <= delta): shaped rule w_s*dS - w_c*dC + w_n*nu > 0
      (a within-noise candidate must pay its way by being cheaper or by
      exploring a new structural component).
    Defaults follow ``rrsi/config.py``.
    """

    name = "cost_rule"

    def __init__(self, beta0: float = 0.10, beta1: float = 40.0, w_s: float = 100.0, w_c: float = 15.0,
                 w_n: float = 0.5, within_band: bool = True) -> None:
        self.beta0, self.beta1 = beta0, beta1
        self.w_s, self.w_c, self.w_n = w_s, w_c, w_n
        self.within_band = within_band

    def check(self, cand, inc, ctx):
        dS = cand.score - inc.score
        dC = (cand.cost - inc.cost) / inc.cost if inc.cost > 0 and cand.cost > 0 else 0.0
        d = {"dS": dS, "dC": dC}
        if dS > ctx.delta + TIE_EPS:
            limit = self.beta0 + self.beta1 * dS
            ok = dC <= limit + TIE_EPS
            return Verdict(ok, f"dC={dC:+.3f} {'<=' if ok else '>'} beta0+beta1*dS={limit:.3f}", d)
        if not self.within_band:
            return Verdict(False, f"dS={dS:+.4f} within noise band", d)
        shaped = self.w_s * dS - self.w_c * dC + self.w_n * cand.novelty
        ok = shaped > TIE_EPS
        return Verdict(ok, f"within band: shaped={shaped:+.3f} ({'>' if ok else '<='} 0)", {**d, "shaped": shaped})


class MetricGuard(Gate):
    """Non-compensatory guard: metric must not drop (or rise) by more than ``tol``
    relative to the incumbent (RRSI Domain.guards, e.g. valid-rate)."""

    def __init__(self, metric: str, tol: float, higher_is_better: bool = True) -> None:
        self.metric, self.tol, self.hib = metric, tol, higher_is_better
        self.name = f"guard:{metric}"

    def check(self, cand, inc, ctx):
        c, i = cand.metrics.get(self.metric), inc.metrics.get(self.metric)
        if c is None or i is None:
            return Verdict(True, "metric missing; guard skipped")
        change = (c - i) if self.hib else (i - c)
        ok = change >= -self.tol - TIE_EPS
        return Verdict(ok, f"{self.metric} change {change:+.4f} (tol {self.tol})")


class RRSIGate(AllOf):
    """RRSI Algorithm 2 admissibility: floor AND cost rule AND domain guards."""

    name = "rrsi"

    def __init__(self, cost_rule: Optional[CostRule] = None, guards: Sequence[Gate] = (), floor: bool = True,
                 cost: bool = True) -> None:
        gates: list[Gate] = []
        if floor:
            gates.append(NoiseFloor())
        if cost:
            gates.append(cost_rule or CostRule())
        else:
            gates.append(MinGainOrEqualWithinBand())
        gates.extend(guards)
        super().__init__(gates)


class MinGainOrEqualWithinBand(Gate):
    """Used when the cost rule is ablated: any candidate is admissible on cost."""

    name = "no_cost_rule"

    def check(self, cand, inc, ctx):
        return Verdict(True, "cost rule disabled")


class BootstrapRigor(Gate):
    """Keep iff P_boot(candidate beats best) >= p over per-seed samples
    (autoresearch-mlx ``rigor.py``: 3 seeds, 20000 resamples, p = 0.95)."""

    name = "bootstrap_rigor"

    def __init__(self, p: float = 0.95, lower_is_better: bool = False, reps: int = 20000) -> None:
        self.p, self.lower, self.reps = p, lower_is_better, reps

    def check(self, cand, inc, ctx):
        if not cand.samples or not inc.samples:
            return Verdict(False, "no samples")
        pb = prob_better_bootstrap(cand.samples, inc.samples, reps=self.reps, lower_is_better=self.lower)
        ok = pb >= self.p
        return Verdict(ok, f"P(better)={pb:.3f} {'>=' if ok else '<'} {self.p}", {"p_better": pb})


class DualGate(Gate):
    """SoL-Pi acceptance: every capability metric within tolerance of the base AND
    at least one efficiency metric strictly improves.

    ``capability``: {metric: tolerance} (higher is better; within tol means
    ``cand >= base - tol``). ``efficiency``: {metric: min relative improvement}
    (lower is better, e.g. tokens, dollars, steps)."""

    name = "dual_gate"

    def __init__(self, capability: dict[str, float], efficiency: dict[str, float]) -> None:
        self.capability = capability
        self.efficiency = efficiency

    def check(self, cand, inc, ctx):
        det = {}
        for m, tol in self.capability.items():
            c, b = cand.metrics.get(m, cand.score if m == "score" else None), inc.metrics.get(m, inc.score if m == "score" else None)
            if c is None or b is None:
                return Verdict(False, f"capability metric {m} missing")
            det[m] = c - b
            if not c >= b - tol - TIE_EPS:   # written so that a NaN capability fails too
                return Verdict(False, f"capability {m} dropped {c - b:+.4f} beyond tol {tol}", det)
        improved = []
        for m, min_rel in self.efficiency.items():
            c, b = cand.metrics.get(m), inc.metrics.get(m)
            if c is None or b is None or b <= 0:
                continue
            rel = (b - c) / b
            det[f"{m}_saving"] = rel
            if rel > min_rel + TIE_EPS:
                improved.append(m)
        ok = bool(improved)
        return Verdict(ok, f"efficiency improved on {improved}" if ok else "no efficiency metric improved", det)


def _is_nan(x) -> bool:
    try:
        return math.isnan(x)
    except TypeError:          # tuple / non-numeric keys
        return False


def select(
    candidates: Sequence[tuple[object, Scored]],
    incumbent: Scored,
    gate: Gate,
    ctx: GateContext,
    key: Callable[[Scored], float] = lambda s: s.score,
) -> tuple[Optional[object], list[tuple[object, Verdict]]]:
    """Return (winner or None, verdicts). Winner = argmax ``key`` over admissible
    candidates (RRSI: H_{t+1} = argmax S over admissible, else keep H_t); ties go
    to the earliest candidate. A candidate whose ``key`` is NaN is never admissible
    (its verdict is replaced), since ``max`` would otherwise return it whenever it
    comes first."""
    verdicts = []
    for c, s in candidates:
        v = gate.check(s, incumbent, ctx)
        if v.accept and _is_nan(key(s)):
            v = Verdict(False, f"invalid: selection key is NaN ({v.reason})", v.details)
        verdicts.append((c, v))
    admissible = [(c, s) for (c, s), (_, v) in zip(candidates, verdicts) if v.accept]
    if not admissible:
        return None, verdicts
    winner = max(admissible, key=lambda cs: key(cs[1]))[0]
    return winner, verdicts
