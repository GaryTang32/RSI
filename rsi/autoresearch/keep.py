"""Keep rules: the main plug-in point of the autoresearch loop.

======================  ============================================================
:class:`StrictKeep`     upstream (faithful default): keep iff the single run is
                        strictly better than the branch tip; equal or worse resets.
:class:`BootstrapRigorKeep`
                        autoresearch-mlx ``rigor.py``: ``repeats`` runs (default 3),
                        keep iff P_boot(mean(cand) beats mean(best)) >= 0.95 with
                        20,000 resamples (rng 1234); a first run no better than the
                        best mean is discarded at once; an identical file is never
                        re-scored. ``vary_seed=True`` (spec suggestion) draws a fresh
                        run seed per repeat; ``False`` repeats the pinned seed as
                        rigor.py does.
:class:`SimplicityWeighted`
                        mechanical proxy for the "simplicity criterion": a gain must
                        pay for added lines; deleting code at ~equal score is kept.
:class:`GateKeep`       any :mod:`rsi.core.gates` gate, e.g. :class:`~rsi.core.RRSIGate`
                        (noise floor + cost rule) - "RRSI fixes the keep rule".
======================  ============================================================

Rules see :class:`Samples` (per-run metric values of candidate and incumbent)
and a :class:`KeepContext`; they return an :class:`rsi.core.Verdict`, so any rule
can be re-adjudicated later from the ledger.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence

import numpy as np

from ..core.gates import BootstrapRigor, Gate, GateContext, RRSIGate, Scored, StrictImprovement, Verdict
from ..core.stats import NoiseEstimate, noise_from_repeats


@dataclass
class Samples:
    """Per-run metric values of one version (one value for single-run rules)."""

    values: list[float]
    memory_gb: float = 0.0
    loc: int = 0                    # lines of code in the editable files
    artifact_id: str = ""
    meta: dict = field(default_factory=dict)

    @property
    def mean(self) -> float:
        return float(np.mean(self.values)) if self.values else float("nan")

    @property
    def last(self) -> float:
        return float(self.values[-1])


@dataclass
class KeepContext:
    direction: str = "min"
    best: Optional[float] = None               # running best metric value (direction-aware)
    delta: float = 0.0                         # noise band (from NoiseCalibrator), metric units
    experiment: int = 0
    lines_added: int = 0
    lines_removed: int = 0
    info: dict = field(default_factory=dict)


class KeepRule:
    """Base class. ``repeats`` = runs needed per candidate (1 for single-run rules)."""

    name = "keep_rule"
    repeats: int = 1
    vary_seed: bool = False
    never_repeat: bool = False

    def early_reject(self, first: float, best: Optional[Samples], direction: str) -> bool:
        """After the first of several runs: discard a clear loser without more runs."""
        return False

    def decide(self, cand: Samples, inc: Samples, ctx: KeepContext) -> Verdict:  # pragma: no cover
        raise NotImplementedError

    def reference(self, incumbent: Samples, keeps: Sequence[Samples], direction: str) -> Samples:
        """Which version a candidate is compared with (default: the branch tip)."""
        return incumbent

    def to_json(self) -> dict:
        return {"name": self.name, "repeats": self.repeats, "vary_seed": self.vary_seed}


def _scored(s: Samples, direction: str, cost: float = 0.0) -> Scored:
    sign = -1.0 if direction == "min" else 1.0
    return Scored(score=sign * s.mean, cost=cost, samples=[sign * v for v in s.values],
                  metrics={"memory_gb": s.memory_gb, "loc": float(s.loc)})


class StrictKeep(KeepRule):
    """Upstream rule: keep iff strictly better than the incumbent (one run each)."""

    name = "strict"

    def __init__(self, min_gain: float = 0.0) -> None:
        self.gate = StrictImprovement(lower_is_better=False, min_gain=min_gain)

    def decide(self, cand, inc, ctx):
        return self.gate.check(_scored(cand, ctx.direction), _scored(inc, ctx.direction), GateContext())


class BootstrapRigorKeep(KeepRule):
    """autoresearch-mlx ``rigor.py`` (defaults: 3 runs, confidence 0.95, 20k resamples)."""

    name = "rigor"

    def __init__(self, repeats: int = 3, confidence: float = 0.95, reps: int = 20000, vary_seed: bool = True,
                 early: bool = True, never_repeat: bool = True) -> None:
        self.repeats = repeats
        self.confidence = confidence
        self.vary_seed = vary_seed
        self.early = early
        self.never_repeat = never_repeat
        self.gate = BootstrapRigor(p=confidence, lower_is_better=False, reps=reps)

    def reference(self, incumbent, keeps, direction):
        # rigor.py: "best" = the kept ledger entry with the best mean
        if not keeps:
            return incumbent
        return (min if direction == "min" else max)(keeps, key=lambda s: s.mean)

    def early_reject(self, first, best, direction):
        if not self.early or best is None:
            return False
        return first >= best.mean if direction == "min" else first <= best.mean

    def decide(self, cand, inc, ctx):
        v = self.gate.check(_scored(cand, ctx.direction), _scored(inc, ctx.direction), GateContext())
        v.details["mean_delta"] = cand.mean - inc.mean
        return v

    def to_json(self):
        return {**super().to_json(), "confidence": self.confidence, "reps": self.gate.reps}


class SimplicityWeighted(KeepRule):
    """Mechanical "simplicity criterion" (upstream program.md):

    * net lines added > 0: keep iff gain > eps * net_added / lines_per_eps
      ("a 0.001 improvement that adds 20 lines of hacky code? Probably not worth it");
    * net lines removed: keep iff gain >= -eps ("improvement of ~0 but much simpler
      code? Keep"; "a 0.001 improvement from deleting code? Definitely keep");
    * otherwise strict improvement.
    """

    name = "simplicity"

    def __init__(self, eps: float = 0.001, lines_per_eps: float = 20.0) -> None:
        self.eps = eps
        self.lines_per_eps = lines_per_eps

    def decide(self, cand, inc, ctx):
        gain = (inc.mean - cand.mean) if ctx.direction == "min" else (cand.mean - inc.mean)
        net = ctx.lines_added - ctx.lines_removed
        if net > 0:
            need = self.eps * net / self.lines_per_eps
            ok = gain > need + 1e-9 * max(1.0, abs(need))
            why = f"gain {gain:+.6f} vs complexity cost {need:.6f} (+{net} lines)"
        elif net < 0:
            ok = gain >= -self.eps
            why = f"simplification ({net} lines): gain {gain:+.6f} >= -eps {self.eps}"
            if not ok:
                why = f"simplification ({net} lines) but gain {gain:+.6f} < -eps {self.eps}"
        else:
            ok = gain > 0
            why = f"gain {gain:+.6f} {'>' if ok else '<='} 0"
        return Verdict(ok, why, {"gain": gain, "net_lines": net})

    def to_json(self):
        return {**super().to_json(), "eps": self.eps, "lines_per_eps": self.lines_per_eps}


class GateKeep(KeepRule):
    """Adapter for any :class:`rsi.core.gates.Gate` (higher-is-better internally;
    min-direction metrics are negated). ``cost`` defaults to peak memory (GB), the
    only cost upstream records; ``delta`` comes from the context (noise band)."""

    def __init__(self, gate: Gate, *, repeats: int = 1, vary_seed: bool = False, cost: str = "memory_gb",
                 name: Optional[str] = None) -> None:
        self.gate = gate
        self.repeats = repeats
        self.vary_seed = vary_seed
        self.cost = cost
        self.name = name or f"gate:{gate.name}"

    def decide(self, cand, inc, ctx):
        c_cost = cand.memory_gb if self.cost == "memory_gb" else float(cand.meta.get(self.cost, 0.0))
        i_cost = inc.memory_gb if self.cost == "memory_gb" else float(inc.meta.get(self.cost, 0.0))
        sign = -1.0 if ctx.direction == "min" else 1.0
        gctx = GateContext(best_score=None if ctx.best is None else sign * ctx.best, delta=ctx.delta,
                           round=ctx.experiment)
        return self.gate.check(_scored(cand, ctx.direction, c_cost), _scored(inc, ctx.direction, i_cost), gctx)


def make_keep_rule(spec, **kw) -> KeepRule:
    """``"strict" | "rigor" | "simplicity" | "rrsi"`` or a :class:`KeepRule` / core :class:`Gate`."""
    if isinstance(spec, KeepRule):
        return spec
    if isinstance(spec, Gate):
        return GateKeep(spec, **kw)
    if spec == "strict":
        return StrictKeep(**kw)
    if spec == "rigor":
        return BootstrapRigorKeep(**kw)
    if spec == "simplicity":
        return SimplicityWeighted(**kw)
    if spec == "rrsi":
        return GateKeep(RRSIGate(**kw), name="rrsi")
    raise ValueError(f"unknown keep rule {spec!r}")


class NoiseCalibrator:
    """"Measure your noise first": re-run the unchanged baseline ``k`` times and
    return the noise band delta = z * sd(difference of two runs)."""

    def __init__(self, k: int = 5, z: float = 2.0) -> None:
        self.k = k
        self.z = z

    def estimate(self, values: Sequence[float]) -> NoiseEstimate:
        return noise_from_repeats(list(values), z=self.z)
