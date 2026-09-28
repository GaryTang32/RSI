"""Keep rules: the main plug-in point of the autoresearch loop.

======================  ============================================================
:class:`UpstreamKeep`   upstream (faithful default, ``"upstream"``): keep iff the
                        single run is strictly better than the branch tip, with the
                        simplicity criterion (net code lines) and the VRAM soft
                        constraint (memory ratio) of ``program.md`` made measurable.
:class:`StrictKeep`     the bare mechanical rule (``"strict"``): keep iff strictly
                        better; equal or worse resets. Ignores code size and memory.
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
    """Bare strict rule: keep iff strictly better than the incumbent (one run each).
    Upstream's loop steps 8-9 without the simplicity / VRAM judgment (see :class:`UpstreamKeep`)."""

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


class UpstreamKeep(KeepRule):
    """The faithful default: upstream ``program.md``'s keep decision made measurable.

    Upstream advances the branch iff val_bpb improved (lower) and resets on equal or
    worse (``program.md:103-104``), and asks the agent to weigh two soft criteria that
    it leaves to judgment (``program.md:35,37``). Because the framework (not the agent)
    decides here, both are given a documented, mechanical form:

    * **Simplicity criterion** (``:37``), measured by the net change in *code* lines of
      the editable files (blank and comment-only lines are not counted):

      - net lines removed: keep iff ``gain >= -eps`` - "removing something and getting
        equal or better results ... simplification win", "an improvement of ~0 but much
        simpler code? Keep" (equal or slightly worse, by at most ``eps``, is kept);
      - net lines added: keep iff ``gain > eps * added / lines_per_eps`` - "a 0.001
        val_bpb improvement that adds 20 lines of hacky code? Probably not worth it"
        (defaults ``eps=0.001``, ``lines_per_eps=20``: a gain must pay 0.001 per 20 lines);
      - no net change (e.g. a one-line knob edit): strict improvement (``gain > 0``).

    * **VRAM soft constraint** (``:35``, peak memory is the ``memory_gb`` column): with
      ``ratio = memory(candidate) / memory(incumbent)``

      - ``ratio > mem_blowup`` (default 2.0): discard - "it should not blow up
        dramatically";
      - ``1 + mem_tol < ratio <= mem_blowup`` (default tolerance 10%): the gain must also
        be "meaningful", i.e. at least ``eps`` more than the threshold above - "some
        increase is acceptable for meaningful val_bpb gains";
      - otherwise memory is ignored. Memory is ignored when either value is 0 (unknown).

    ``eps`` is in metric units (upstream's own example unit, 0.001 val_bpb). With no
    line change and no memory change the rule is exactly :class:`StrictKeep`.
    """

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
        gain = (inc.mean - cand.mean) if ctx.direction == "min" else (cand.mean - inc.mean)
        net = ctx.lines_added - ctx.lines_removed
        details = {"gain": gain, "net_lines": net, "eps": self.eps}
        parts = []
        # 1) simplicity criterion
        if self.simplicity and net > 0:
            thr, inclusive = self.eps * net / self.lines_per_eps, False
            parts.append(f"+{net} code lines cost {thr:.6f}")
        elif self.simplicity and net < 0:
            thr, inclusive = -self.eps, True
            parts.append(f"simplification ({net} code lines): equal or slightly worse (>= -{self.eps:g}) is kept")
        else:
            thr, inclusive = 0.0, False
        # 2) VRAM soft constraint
        ratio = None
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
    """``"upstream" | "strict" | "rigor" | "simplicity" | "rrsi"`` or a :class:`KeepRule` / core :class:`Gate`."""
    if isinstance(spec, KeepRule):
        return spec
    if isinstance(spec, Gate):
        return GateKeep(spec, **kw)
    if spec == "upstream":
        return UpstreamKeep(**kw)
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
