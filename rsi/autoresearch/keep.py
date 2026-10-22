"""Keep rules: decide whether a candidate advances the branch."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence

import numpy as np

from ..core.gates import BootstrapRigor, Gate, GateContext, RRSIGate, Scored, StrictImprovement, Verdict
from ..core.stats import NoiseEstimate, noise_from_repeats


@dataclass
class Samples:
    """Per-run metric values of one version."""

    values: list[float]
    memory_gb: float = 0.0
    loc: int = 0                    # code lines in the editable files
    artifact_id: str = ""
    meta: dict = field(default_factory=dict)

    @property
    def mean(self) -> float:
        return float(np.mean(self.values)) if self.values else float("nan")


@dataclass
class KeepContext:
    direction: str = "min"
    best: Optional[float] = None               # running best (direction-aware)
    delta: float = 0.0                         # noise band in metric units
    experiment: int = 0
    lines_added: int = 0
    lines_removed: int = 0
    info: dict = field(default_factory=dict)


class KeepRule:
    """Base class; ``repeats`` is the number of runs needed per candidate."""

    name = "keep_rule"
    repeats: int = 1
    vary_seed: bool = False
    never_repeat: bool = False

    def early_reject(self, first: float, best: Optional[Samples], direction: str) -> bool:
        return False

    def decide(self, cand: Samples, inc: Samples, ctx: KeepContext) -> Verdict:  # pragma: no cover
        raise NotImplementedError

    def reference(self, incumbent: Samples, keeps: Sequence[Samples], direction: str) -> Samples:
        return incumbent

    def to_json(self) -> dict:
        return {"name": self.name, "repeats": self.repeats, "vary_seed": self.vary_seed}


def _scored(s: Samples, direction: str, cost: float = 0.0) -> Scored:
    sign = -1.0 if direction == "min" else 1.0
    return Scored(score=sign * s.mean, cost=cost, samples=[sign * v for v in s.values],
                  metrics={"memory_gb": s.memory_gb, "loc": float(s.loc)})


def _gain(cand: Samples, inc: Samples, direction: str) -> float:
    return (inc.mean - cand.mean) if direction == "min" else (cand.mean - inc.mean)


class StrictKeep(KeepRule):
    """Keep iff strictly better than the incumbent."""

    name = "strict"

    def __init__(self, min_gain: float = 0.0) -> None:
        self.gate = StrictImprovement(lower_is_better=False, min_gain=min_gain)

    def decide(self, cand, inc, ctx):
        return self.gate.check(_scored(cand, ctx.direction), _scored(inc, ctx.direction), GateContext())


class BootstrapRigorKeep(KeepRule):
    """autoresearch-mlx ``rigor.py``: keep iff P_boot(cand beats best kept mean) >= confidence."""

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
    """Added lines must be paid for (``eps`` per ``lines_per_eps``); net deletions are kept if ``gain >= -eps``."""

    name = "simplicity"

    def __init__(self, eps: float = 0.001, lines_per_eps: float = 20.0) -> None:
        self.eps = eps
        self.lines_per_eps = lines_per_eps

    def decide(self, cand, inc, ctx):
        gain = _gain(cand, inc, ctx.direction)
        net = ctx.lines_added - ctx.lines_removed
        if net > 0:
            need = self.eps * net / self.lines_per_eps
            ok = gain > need + 1e-9 * max(1.0, abs(need))
            why = f"gain {gain:+.6f} vs complexity cost {need:.6f} (+{net} lines)"
        elif net < 0:
            ok = gain >= -self.eps
            why = (f"simplification ({net} lines): gain {gain:+.6f} >= -eps {self.eps}" if ok
                   else f"simplification ({net} lines) but gain {gain:+.6f} < -eps {self.eps}")
        else:
            ok = gain > 0
            why = f"gain {gain:+.6f} {'>' if ok else '<='} 0"
        return Verdict(ok, why, {"gain": gain, "net_lines": net})

    def to_json(self):
        return {**super().to_json(), "eps": self.eps, "lines_per_eps": self.lines_per_eps}


class UpstreamKeep(KeepRule):
    """Strict keep plus program.md's simplicity criterion and VRAM soft constraint, made mechanical."""

    name = "upstream"

    def __init__(self, eps: float = 0.001, lines_per_eps: float = 20.0, mem_tol: float = 0.10,
                 mem_blowup: float = 2.0, simplicity: bool = True, memory: bool = True) -> None:
        self.eps = eps
        self.lines_per_eps = lines_per_eps
        self.mem_tol = mem_tol
        self.mem_blowup = mem_blowup
        self.simplicity = simplicity
        self.memory = memory

    def decide(self, cand, inc, ctx):
        gain = _gain(cand, inc, ctx.direction)
        net = ctx.lines_added - ctx.lines_removed
        details = {"gain": gain, "net_lines": net, "eps": self.eps}
        parts = []
        # simplicity criterion (program.md:37): added code lines raise the bar, net deletions lower it to -eps
        if self.simplicity and net > 0:
            thr, inclusive = self.eps * net / self.lines_per_eps, False
            parts.append(f"+{net} code lines cost {thr:.6f}")
        elif self.simplicity and net < 0:
            thr, inclusive = -self.eps, True
            parts.append(f"simplification ({net} code lines): equal or slightly worse (>= -{self.eps:g}) is kept")
        else:
            thr, inclusive = 0.0, False
        # VRAM soft constraint (program.md:35): a blow-up is discarded, a moderate increase needs +eps more gain
        if self.memory and cand.memory_gb > 0 and inc.memory_gb > 0:
            ratio = cand.memory_gb / inc.memory_gb
            details["memory_ratio"] = ratio
            if ratio > self.mem_blowup:
                why = (f"memory blew up {inc.memory_gb:g} -> {cand.memory_gb:g} GB (x{ratio:.2f} > "
                       f"x{self.mem_blowup:g}); gain {gain:+.6f}")
                return Verdict(False, why, {**details, "threshold": None, "memory_blowup": True})
            if ratio > 1.0 + self.mem_tol:
                thr = max(thr, 0.0) + self.eps
                inclusive = True
                parts.append(f"memory x{ratio:.2f} needs a meaningful gain (+{self.eps:g})")
        tol = 1e-9 * max(1.0, abs(thr))
        ok = gain >= thr - tol if inclusive else gain > thr + tol
        details["threshold"] = thr
        why = f"gain {gain:+.6f} {'>=' if inclusive else '>'} {thr:+.6f}: {'keep' if ok else 'discard'}"
        if parts:
            why += " (" + "; ".join(parts) + ")"
        return Verdict(ok, why, details)

    def to_json(self):
        return {**super().to_json(), "eps": self.eps, "lines_per_eps": self.lines_per_eps, "mem_tol": self.mem_tol,
                "mem_blowup": self.mem_blowup, "simplicity": self.simplicity, "memory": self.memory}


class GateKeep(KeepRule):
    """Adapter for any core :class:`Gate`; cost defaults to peak memory, ``delta`` comes from the context."""

    def __init__(self, gate: Gate, *, repeats: int = 1, vary_seed: bool = False, cost: str = "memory_gb",
                 name: Optional[str] = None) -> None:
        self.gate = gate
        self.repeats = repeats
        self.vary_seed = vary_seed
        self.cost = cost
        self.name = name or f"gate:{gate.name}"

    def _cost(self, s: Samples) -> float:
        return s.memory_gb if self.cost == "memory_gb" else float(s.meta.get(self.cost, 0.0))

    def decide(self, cand, inc, ctx):
        sign = -1.0 if ctx.direction == "min" else 1.0
        gctx = GateContext(best_score=None if ctx.best is None else sign * ctx.best, delta=ctx.delta,
                           round=ctx.experiment)
        return self.gate.check(_scored(cand, ctx.direction, self._cost(cand)),
                               _scored(inc, ctx.direction, self._cost(inc)), gctx)


def make_keep_rule(spec, **kw) -> KeepRule:
    """``"upstream" | "strict" | "rigor" | "simplicity" | "rrsi"`` or a :class:`KeepRule` / core :class:`Gate`."""
    if isinstance(spec, KeepRule):
        return spec
    if isinstance(spec, Gate):
        return GateKeep(spec, **kw)
    if spec == "rrsi":
        return GateKeep(RRSIGate(**kw), name="rrsi")
    rules = {"upstream": UpstreamKeep, "strict": StrictKeep, "rigor": BootstrapRigorKeep,
             "simplicity": SimplicityWeighted}
    rule = rules.get(spec) if isinstance(spec, str) else None
    if rule is None:
        raise ValueError(f"unknown keep rule {spec!r}")
    return rule(**kw)


class NoiseCalibrator:
    """Noise band from ``k`` baseline re-runs: delta = z * sd(difference of two runs)."""

    def __init__(self, k: int = 5, z: float = 2.0) -> None:
        self.k = k
        self.z = z

    def estimate(self, values: Sequence[float]) -> NoiseEstimate:
        return noise_from_repeats(list(values), z=self.z)
