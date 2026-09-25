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

:func:`build_gates` maps :class:`~rsi.rrsi.switches.RegularizerSwitches` to core
gates: :class:`rsi.core.NoiseFloor` (S*), :class:`IncumbentFloor` (S_t, ablation),
:class:`RRSICostRule` (a :class:`rsi.core.CostRule` with ablatable branches), domain
guards (e.g. :class:`rsi.core.MetricGuard`), or :class:`rsi.core.StrictImprovement`
for the unregularized "keep if better" baseline.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional, Sequence

from ..core.gates import (TIE_EPS, AllOf, CostRule, Gate, GateContext, NoiseFloor, Scored, StrictImprovement,
                          Verdict, select)
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


class IncumbentFloor(Gate):
    """Ablation (E4): floor relative to the CURRENT incumbent, S' >= S_t - delta."""

    name = "incumbent_floor"

    def check(self, cand, inc, ctx):
        floor = inc.score - ctx.delta
        ok = cand.score >= floor - TIE_EPS                              # same float-tie tolerance as NoiseFloor
        return Verdict(ok, f"S'={cand.score:.4f} {'>=' if ok else '<'} S_t-delta={floor:.4f}", {"floor": floor})


class RRSICostRule(CostRule):
    """The core cost rule with ablatable branches.

    ``above_band``: apply dC <= beta0 + beta1*dS to gains above delta (False = any cost passes).
    ``in_band``: ``shaped`` (paper), ``strict`` (admit iff dS > 0), ``reject`` or ``admit``."""

    def __init__(self, beta0=0.10, beta1=40.0, w_s=100.0, w_c=15.0, w_n=0.5, *, above_band: bool = True,
                 in_band: str = "shaped") -> None:
        super().__init__(beta0, beta1, w_s, w_c, w_n, within_band=in_band != "reject")
        self.above_band = above_band
        self.in_band = in_band

    def check(self, cand, inc, ctx):
        dS = cand.score - inc.score
        dC = relative_cost_change(cand.cost, inc.cost)
        if dS > ctx.delta + TIE_EPS:                                    # the band test of the core CostRule
            if not self.above_band:
                return Verdict(True, f"gain {dS:+.4f} > delta; cost rule disabled", {"dS": dS, "dC": dC})
            return super().check(cand, inc, ctx)
        if self.in_band == "shaped" or self.in_band == "reject":
            return super().check(cand, inc, ctx)
        if self.in_band == "admit":
            return Verdict(True, f"gain {dS:+.4f} within band; within-band rule disabled", {"dS": dS, "dC": dC})
        ok = dS > TIE_EPS                                               # "strict"
        return Verdict(ok, f"within band: dS={dS:+.4f} {'>' if ok else '<='} 0 (strict rule)", {"dS": dS, "dC": dC})


REASON_PREFIX = {"noise_floor": "below noise-adjusted floor", "incumbent_floor": "below incumbent-relative floor",
                 "cost_rule": "cost rule failed", "strict_improvement": "not better than the incumbent"}


def _prefix(g: Gate) -> str:
    if g.name.startswith("guard") or getattr(g, "is_domain_guard", False):
        return "domain guard violated"
    return REASON_PREFIX.get(g.name, g.name)


def build_gates(cfg, switches: Optional[RegularizerSwitches] = None, guards: Sequence[Gate] = ()) -> list[Gate]:
    """Algorithm 2 admissibility as an ordered list of core gates."""
    sw = switches or RegularizerSwitches.full()
    if sw.selection == "greedy":
        return [StrictImprovement()]
    if sw.selection == "argmax":
        return []
    gates: list[Gate] = []
    if sw.floor == "S_star":
        gates.append(NoiseFloor())
    elif sw.floor == "S_t":
        gates.append(IncumbentFloor())
    gates.append(RRSICostRule(cfg.beta0, cfg.beta1, cfg.w_s, cfg.w_c, cfg.w_n, above_band=sw.cost_rule,
                              in_band=sw.within_band))
    if sw.domain_guards:
        for g in guards:
            setattr(g, "is_domain_guard", True)
            gates.append(g)
    return gates


def cost_rule(delta_S: float, delta_C: float, nov: int, delta: float, cfg) -> tuple[bool, str]:
    """c of Algorithm 2, line 5 (pure; the code's signature)."""
    g = CostRule(cfg.beta0, cfg.beta1, cfg.w_s, cfg.w_c, cfg.w_n)
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
