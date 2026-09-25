"""Algorithm 2 of the paper, the selection side, as pure functions (mirrors
``rrsi/selection.py``), composed from :mod:`rsi.core.gates`.

For every screened candidate H' with measurement (S', C') against the incumbent
(S_t, C_t)::

    dS = S' - S_t,   dC = (C' - C_t) / C_t        (0 if either token count is missing)
    c  = [dC <= beta0 + beta1 * dS]                  if dS > delta      Eq. (tokenbudget)
         [w_s * dS - w_c * dC + w_n * nu(l') > 0]    otherwise          (Alg. 2 line 5)
    admissible  iff  S' >= S* - delta  and  c  and  every domain guard holds   Eq. (floor)
    H_{t+1} = argmax_{admissible} S'  (first on ties),  or H_t if none is admissible
    S*      = max(S*, S_{t+1})

Check order and reason strings follow the code: gate failure (critic_reject /
smoke_fail / eval_invalid / no_proposal), "below noise-adjusted floor", "cost rule
failed", "domain guard violated". The winner is the argmax of S' among admissible
candidates, not of the shaped score, and H_t itself is never a candidate.

Comparisons are made on the raw floats, exactly as in the code (``delta_S > delta``,
``S' < floor``, ``delta_C <= budget``, ``shaped > 0``). ``Config.tie_eps`` (default 0.0)
is the extension that treats differences below it as ties (``1e-9`` = the
:data:`rsi.core.gates.TIE_EPS` reading, under which 0.55 - 0.50 = 0.05000000000000004
counts as "not above delta = 0.05"); it changes decisions only at exact ties.

:func:`build_gates` maps :class:`~rsi.rrsi.switches.RegularizerSwitches` to gates:
:class:`RRSINoiseFloor` (S*), :class:`IncumbentFloor` (S_t, ablation),
:class:`RRSICostRule` (the cost rule with ablatable branches), domain guards (e.g.
:class:`rsi.core.MetricGuard`), or :class:`GreedyImprovement` for the unregularized
"keep if better" baseline. They are :class:`rsi.core.Gate` subclasses, so the core
:func:`rsi.core.select` and every other method can reuse them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional, Sequence

from ..core.gates import AllOf, CostRule, Gate, GateContext, Scored, Verdict, select
from .components import Taxonomy
from .evaluate import Measurement, relative_cost_change
from .switches import RegularizerSwitches


@dataclass
class Candidate:
    variant: str
    edits: list[dict]                       # each tagged (component, hypothesis, ...)
    ev: Optional[Measurement] = None
    diff_path: Optional[str] = None
    artifact_id: Optional[str] = None
    gate_failure: Optional[str] = None      # critic_reject / smoke_fail / eval_invalid / no_proposal
    detail: str = ""
    mechanism: str = ""

    @property
    def components(self) -> list[str]:
        return [e.get("component") for e in self.edits if e.get("component")]


@dataclass
class Decision:
    variant: str
    admissible: bool
    reason: str
    S: Optional[float] = None
    C: Optional[float] = None
    delta_S: Optional[float] = None
    delta_C: Optional[float] = None
    novelty: int = 0
    guards: list = field(default_factory=list)

    def to_json(self) -> dict:
        return dict(self.__dict__)


class RRSINoiseFloor(Gate):
    """Eq. (floor): S' >= S* - delta, S* = the best incumbent score so far (code: reject iff S' < floor)."""

    name = "noise_floor"

    def __init__(self, tie_eps: float = 0.0) -> None:
        self.tie_eps = float(tie_eps)

    def check(self, cand, inc, ctx):
        ref = ctx.best_score if ctx.best_score is not None else inc.score
        floor = ref - ctx.delta
        ok = cand.score >= floor - self.tie_eps
        return Verdict(ok, f"S'={cand.score:.4f} {'>=' if ok else '<'} S*-delta={floor:.4f}", {"floor": floor})


class IncumbentFloor(RRSINoiseFloor):
    """Ablation (E4): floor relative to the CURRENT incumbent, S' >= S_t - delta."""

    name = "incumbent_floor"

    def check(self, cand, inc, ctx):
        floor = inc.score - ctx.delta
        ok = cand.score >= floor - self.tie_eps
        return Verdict(ok, f"S'={cand.score:.4f} {'>=' if ok else '<'} S_t-delta={floor:.4f}", {"floor": floor})


class RRSICostRule(CostRule):
    """c of Algorithm 2 line 5, with ablatable branches::

        dS > delta:  dC <= beta0 + beta1 * dS                  (Eq. tokenbudget)
        otherwise:   w_s * dS - w_c * dC + w_n * nu > 0        (within the noise band)

    ``above_band``: apply the cost rule to gains above delta (False = any cost passes).
    ``in_band``: ``shaped`` (paper), ``strict`` (admit iff dS > 0), ``reject`` or ``admit``.
    ``tie_eps``: 0.0 = raw float comparisons, as in the code (see the module docstring)."""

    def __init__(self, beta0=0.10, beta1=40.0, w_s=100.0, w_c=15.0, w_n=0.5, *, above_band: bool = True,
                 in_band: str = "shaped", tie_eps: float = 0.0) -> None:
        super().__init__(beta0, beta1, w_s, w_c, w_n, within_band=in_band != "reject")
        self.above_band = above_band
        self.in_band = in_band
        self.tie_eps = float(tie_eps)

    def check(self, cand, inc, ctx):
        eps = self.tie_eps
        dS = cand.score - inc.score
        dC = relative_cost_change(cand.cost, inc.cost)
        d = {"dS": dS, "dC": dC}
        if dS > ctx.delta + eps:
            if not self.above_band:
                return Verdict(True, f"gain {dS:+.4f} > delta; cost rule disabled", d)
            limit = self.beta0 + self.beta1 * dS
            ok = dC <= limit + eps
            return Verdict(ok, f"dC={dC:+.3f} {'<=' if ok else '>'} beta0+beta1*dS={limit:.3f}", d)
        if self.in_band == "reject":
            return Verdict(False, f"dS={dS:+.4f} within noise band", d)
        if self.in_band == "admit":
            return Verdict(True, f"gain {dS:+.4f} within band; within-band rule disabled", d)
        if self.in_band == "strict":
            ok = dS > eps
            return Verdict(ok, f"within band: dS={dS:+.4f} {'>' if ok else '<='} 0 (strict rule)", d)
        shaped = self.w_s * dS - self.w_c * dC + self.w_n * cand.novelty
        ok = shaped > eps
        return Verdict(ok, f"within band: shaped={shaped:+.3f} ({'>' if ok else '<='} 0)", {**d, "shaped": shaped})


class GreedyImprovement(Gate):
    """The unregularized baseline's keep rule: S' > S_t (no floor, no cost rule)."""

    name = "strict_improvement"

    def __init__(self, tie_eps: float = 0.0) -> None:
        self.tie_eps = float(tie_eps)

    def check(self, cand, inc, ctx):
        gain = cand.score - inc.score
        ok = gain > self.tie_eps
        return Verdict(ok, f"gain {gain:+.4f} {'>' if ok else '<='} 0.0", {"gain": gain})


REASON_PREFIX = {"noise_floor": "below noise-adjusted floor", "incumbent_floor": "below incumbent-relative floor",
                 "cost_rule": "cost rule failed", "strict_improvement": "not better than the incumbent"}


def _prefix(g: Gate) -> str:
    if g.name.startswith("guard") or getattr(g, "is_domain_guard", False):
        return "domain guard violated"
    return REASON_PREFIX.get(g.name, g.name)


def build_gates(cfg, switches: Optional[RegularizerSwitches] = None, guards: Sequence[Gate] = ()) -> list[Gate]:
    """Algorithm 2 admissibility as an ordered list of gates (raw float comparisons unless ``cfg.tie_eps``)."""
    sw = switches or RegularizerSwitches.full()
    eps = float(getattr(cfg, "tie_eps", 0.0) or 0.0)
    if sw.selection == "greedy":
        return [GreedyImprovement(eps)]
    if sw.selection == "argmax":
        return []
    gates: list[Gate] = []
    if sw.floor == "S_star":
        gates.append(RRSINoiseFloor(eps))
    elif sw.floor == "S_t":
        gates.append(IncumbentFloor(eps))
    gates.append(RRSICostRule(cfg.beta0, cfg.beta1, cfg.w_s, cfg.w_c, cfg.w_n, above_band=sw.cost_rule,
                              in_band=sw.within_band, tie_eps=eps))
    if sw.domain_guards:
        for g in guards:
            setattr(g, "is_domain_guard", True)
            gates.append(g)
    return gates


def cost_rule(delta_S: float, delta_C: float, nov: int, delta: float, cfg) -> tuple[bool, str]:
    """c of Algorithm 2, line 5 (pure; the code's signature)."""
    g = RRSICostRule(cfg.beta0, cfg.beta1, cfg.w_s, cfg.w_c, cfg.w_n,
                     tie_eps=float(getattr(cfg, "tie_eps", 0.0) or 0.0))
    inc = Scored(score=0.0, cost=1.0)
    cand = Scored(score=delta_S, cost=1.0 + delta_C, novelty=nov)
    v = g.check(cand, inc, GateContext(delta=delta))
    return v.accept, v.reason


def judge(cand: Candidate, incumbent: Measurement, S_star: float, delta: float, cfg, incumbent_counts: dict,
          gates: Optional[Sequence[Gate]] = None, *, taxonomy: Optional[Taxonomy] = None, t: int = 0) -> Decision:
    """Decision for one candidate (gate failures first, then the admissibility gates in order)."""
    ev = cand.ev
    if ev is None:
        return Decision(cand.variant, False, cand.gate_failure or "not evaluated")
    tax = taxonomy or Taxonomy()
    gates = build_gates(cfg) if gates is None else gates
    dS = ev.S - incumbent.S
    dC = relative_cost_change(ev.C, incumbent.C)
    nov = tax.novelty(cand.components, incumbent_counts)
    d = Decision(cand.variant, False, "", S=ev.S, C=ev.C, delta_S=dS, delta_C=dC, novelty=nov)
    c_sc, i_sc = ev.as_scored(nov), incumbent.as_scored()
    ctx = GateContext(best_score=S_star, delta=delta, round=t)
    passed = []
    for g in gates:
        v = g.check(c_sc, i_sc, ctx)
        if not v.accept:
            if _prefix(g) == "domain guard violated":
                d.guards.append(v.reason)
            d.reason = f"{_prefix(g)}: {v.reason}"
            if g.name == "noise_floor":
                d.reason += f" (S* {S_star:.4f} - delta {delta:.4f})"
            return d
        passed.append(v.reason)
    d.admissible = True
    d.reason = "admissible: " + ("; ".join(passed[-2:]) if passed else "no gates")
    return d


def select_round(cands: Sequence[Candidate], incumbent: Measurement, S_star: float, delta: float, cfg,
                 incumbent_counts: dict, gates: Optional[Sequence[Gate]] = None, *,
                 taxonomy: Optional[Taxonomy] = None, t: int = 0) -> tuple[Optional[Candidate], list[Decision]]:
    """Judge every candidate; winner = argmax S' over admissible ones (via :func:`rsi.core.select`)."""
    gates = build_gates(cfg) if gates is None else list(gates)
    tax = taxonomy or Taxonomy()
    decisions = [judge(c, incumbent, S_star, delta, cfg, incumbent_counts, gates, taxonomy=tax, t=t) for c in cands]
    adm = [(c, c.ev.as_scored(d.novelty)) for c, d in zip(cands, decisions) if d.admissible]
    if not adm:
        return None, decisions
    # All candidates here already passed the gates; core select() is the argmax (first on ties).
    winner, _ = select(adm, incumbent.as_scored(), AllOf([]), GateContext(best_score=S_star, delta=delta, round=t))
    return winner, decisions


GuardFn = Callable[[Measurement, Measurement], list]
