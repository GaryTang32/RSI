"""Mutation, strategy presets and personality (spec §3.4, §4.7).

* :data:`STRATEGIES` - intent mixes per preset (code values, not the README's);
* :class:`StrategyPolicy` - preset resolution (``early-stabilize`` for cycles 1-5,
  ``steady-state`` on saturation) and the adaptive per-cycle policy (max files,
  force-innovate, cautious);
* :class:`MutationBuilder` - category (repair / innovate / explore / optimize) and
  risk level with the personality safety downgrades;
* :class:`PersonalityModel` - natural selection over personality keys, triggered
  mutations, force_pivot on plateaus.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Optional, Sequence

from .assets import Gene, Mutation, PersonalityState, PERSONALITY_TRAITS
from .signals import ERROR_SIGNALS, OPPORTUNITY_SIGNALS, expand_signals

STRATEGIES = {
    "balanced": {"repair": 0.20, "optimize": 0.20, "innovate": 0.50, "explore": 0.10, "repairLoopThreshold": 0.5},
    "innovate": {"repair": 0.05, "optimize": 0.10, "innovate": 0.80, "explore": 0.05, "repairLoopThreshold": 0.3},
    "harden": {"repair": 0.40, "optimize": 0.35, "innovate": 0.20, "explore": 0.05, "repairLoopThreshold": 0.7},
    "repair-only": {"repair": 0.80, "optimize": 0.18, "innovate": 0.00, "explore": 0.02, "repairLoopThreshold": 1.0},
    "early-stabilize": {"repair": 0.60, "optimize": 0.22, "innovate": 0.15, "explore": 0.03,
                        "repairLoopThreshold": 0.8},
    "steady-state": {"repair": 0.55, "optimize": 0.25, "innovate": 0.05, "explore": 0.15, "repairLoopThreshold": 0.9},
}


@dataclass
class Policy:
    preset: str
    max_files: int
    force_innovate: bool
    cautious: bool
    directives: list[str] = field(default_factory=list)

    @property
    def blast_estimate(self) -> dict:
        return {"files": self.max_files, "lines": 80 * self.max_files}


def _ev(e):
    return e.to_dict() if hasattr(e, "to_dict") else e


class StrategyPolicy:
    def __init__(self, preset: str = "balanced", force_innovation: bool = False) -> None:
        self.preset = preset
        self.force_innovation = force_innovation

    def resolve(self, cycle: int, signals: Sequence[str] = ()) -> str:
        name = self.preset
        if name in ("balanced", "auto"):
            if cycle <= 5:
                return "early-stabilize"
            if "force_steady_state" in signals or "evolution_saturation" in signals:
                return "steady-state"
            return "balanced"
        return name if name in STRATEGIES else "balanced"

    def adaptive(self, recent: Sequence, gene: Optional[Gene], signals: Sequence[str], cycle: int) -> Policy:
        ev = [_ev(e) for e in recent][-8:]

        def streak(pred) -> int:
            n = 0
            for e in reversed(ev):
                if pred(e):
                    n += 1
                else:
                    break
            return n

        repair_streak = streak(lambda e: e.get("intent") == "repair")
        failure_streak = streak(lambda e: (e.get("outcome") or {}).get("status") == "failed")
        high_risk_gene = False
        if gene is not None:
            ex = expand_signals(signals)
            aps = [a for a in (gene.anti_patterns or [])[-5:] if set(a.get("learning_signals", [])) & ex]
            hard = sum(1 for a in aps if a.get("mode") == "hard")
            soft = len(aps) - hard
            succ = sum(1 for h in (gene.learning_history or [])[-6:] if h.get("outcome") == "success")
            high_risk_gene = hard >= 1 or (soft >= 2 and succ == 0)
        plateau = any(s in signals for s in ("stable_success_plateau", "evolution_saturation",
                                               "empty_cycle_loop_detected"))
        force = (plateau or failure_streak >= 3 or repair_streak >= 3) and "log_error" not in signals
        cautious = high_risk_gene or failure_streak >= 2
        mf = gene.max_files if gene is not None else 12
        if cautious:
            mf = min(6, max(2, mf))
        elif force:
            mf = min(10, max(3, mf))
        preset = self.resolve(cycle, signals)
        d = [f"Base strategy: {preset}.", f"Target max files for this cycle: {mf}."]
        if force:
            d.append("Force strategy shift: prefer innovate over repeating repair/optimize.")
        return Policy(preset, mf, force, cautious, d)


class MutationBuilder:
    def __init__(self) -> None:
        self._n = itertools.count(1)

    def build(self, signals: Sequence[str], gene: Optional[Gene], *, innovate_mode: bool,
              personality: PersonalityState, allow_high_risk: bool = False, preset: str = "balanced",
              clock_s: float = 0.0) -> Mutation:
        sig = list(signals)
        err = any(s in ERROR_SIGNALS or s.startswith(("errsig:", "errsig_norm:")) for s in sig) and not (
            "issue_already_resolved" in sig or "openclaw_self_healed" in sig)
        opp = any(s in OPPORTUNITY_SIGNALS or s.split(":", 1)[0] in OPPORTUNITY_SIGNALS for s in sig)
        if err:
            cat = "repair"
        elif innovate_mode or opp:
            cat = "innovate"
        elif "explore_opportunity" in sig:
            cat = "explore"
        elif STRATEGIES.get(preset, STRATEGIES["balanced"])["innovate"] >= 0.5:
            cat = "innovate"
        else:
            cat = "optimize"
        risk = "medium" if cat == "innovate" else "low"
        if allow_high_risk and cat == "innovate":
            risk = "high"
        extra = []
        if cat == "innovate" and (personality.rigor < 0.5 or personality.risk_tolerance > 0.6):
            cat, risk = "optimize", "low"
            extra.append("safety:avoid_innovate_with_high_risk_personality")
        if risk == "high" and not (personality.rigor >= 0.6 and personality.risk_tolerance <= 0.5):
            risk = "medium"
            extra.append("safety:downgrade_high_risk")
        effect = {"repair": "reduce runtime errors and restore stability",
                  "optimize": "improve efficiency or robustness without changing behaviour",
                  "innovate": "add a capability or new strategy",
                  "explore": "probe an unexplored approach"}[cat]
        return Mutation(id=f"mut_{int(clock_s)}_{next(self._n)}", category=cat,
                        trigger_signals=list(dict.fromkeys(sig + extra)),
                        target=f"gene:{gene.id}" if gene is not None else "behavior:protocol",
                        expected_effect=effect, risk_level=risk)


class PersonalityModel:
    """Personality evolution: natural selection + triggered mutations (+ force_pivot)."""

    def __init__(self, state: Optional[PersonalityState] = None) -> None:
        self.state = state or PersonalityState()
        self.stats: dict[str, dict] = {}
        self.known = False

    def update_stats(self, state: PersonalityState, success: bool, score: float) -> None:
        st = self.stats.setdefault(state.key(), {"s": 0, "n": 0, "avg": 0.0, "state": state.to_dict()})
        st["n"] += 1
        st["s"] += int(success)
        st["avg"] += (score - st["avg"]) / st["n"]
        self.known = True

    def _apply(self, deltas: dict) -> None:
        for t, d in list(deltas.items())[:2]:
            setattr(self.state, t, getattr(self.state, t) + max(-0.2, min(0.2, d)))
        self.state.clamp()

    def select_for_run(self, drift: bool, signals: Sequence[str], recent_success: Sequence[bool]) -> PersonalityState:
        # 1. natural selection
        cands = [(k, v) for k, v in self.stats.items() if v["n"] >= 3]
        if cands:
            def fit(v):
                return 0.75 * (v["s"] + 1) / (v["n"] + 2) + 0.25 * v["avg"] * min(1.0, v["n"] / 8)
            best = max(cands, key=lambda kv: fit(kv[1]))[1]["state"]
            moves = {}
            for t in PERSONALITY_TRAITS:
                d = float(best.get(t, getattr(self.state, t))) - getattr(self.state, t)
                if abs(d) >= 0.05:
                    moves[t] = max(-0.1, min(0.1, d))
            self._apply(dict(sorted(moves.items(), key=lambda kv: -abs(kv[1]))[:2]))
        # 2. triggered mutation
        rs = list(recent_success)
        trig = drift or (len(rs) >= 4 and sum(1 for x in rs[-4:] if not x) >= 3) or (
            len(rs) >= 3 and not any(rs[-3:]))
        if trig:
            if drift:
                self._apply({"creativity": 0.1, "risk_tolerance": -0.05})
            elif "protocol_drift" in signals:
                self._apply({"obedience": 0.1, "rigor": 0.05})
            elif any(s in ERROR_SIGNALS or s.startswith("errsig:") for s in signals):
                self._apply({"rigor": 0.1, "risk_tolerance": -0.1})
            elif any(s in OPPORTUNITY_SIGNALS for s in signals):
                self._apply({"creativity": 0.1, "risk_tolerance": 0.05})
            else:
                self._apply({"creativity": 0.05, "verbosity": -0.05})
        return self.state.copy()

    def force_pivot(self, severity: str) -> None:
        if severity == "required":
            self._apply({"creativity": 0.2, "risk_tolerance": 0.15})
        elif severity == "suggested":
            self._apply({"creativity": 0.15, "risk_tolerance": 0.1})
