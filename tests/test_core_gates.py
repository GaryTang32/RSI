"""rsi.core.gates: every keep rule, including RRSI's floor / cost / within-band
edge cases (checked against the formulas in rrsi/selection.py), DualGate,
BootstrapRigor, composition and select()."""
from __future__ import annotations

import pytest

from rsi.core import (AllOf, BootstrapRigor, CostRule, DualGate, EvalResult, GateContext, ImprovementOrEqual,
                      MetricGuard, MinGain, NoiseFloor, RRSIGate, Scored, StrictImprovement, Trial, Verdict, select)


def S(score, cost=100.0, **kw):
    return Scored(score=score, cost=cost, **kw)


CTX = GateContext(best_score=None, delta=0.02)


# ------------------------------------------------------------------- Scored
def test_scored_from_eval():
    trials = {"a": [Trial("a", 0, 1.0, tokens=10), Trial("a", 1, 0.0, tokens=30)],
              "b": [Trial("b", 0, 1.0, tokens=20), Trial("b", 1, 1.0, steps=4)]}
    ev = EvalResult("x", "evolve", trials, k=2)
    sc = Scored.from_eval(ev, novelty=2, metrics={"valid_rate": 0.9})
    assert sc.score == pytest.approx(0.75) and sc.cost == pytest.approx(20.0)
    assert sc.samples == [1.0, 0.5]                     # per-seed means (bootstrap rules)
    assert sc.per_task == {"a": 0.5, "b": 1.0}
    assert sc.novelty == 2 and sc.metrics["valid_rate"] == 0.9 and sc.metrics["score"] == sc.score
    assert Scored.from_eval(EvalResult("x", "evolve", {}, k=1)).samples == [0.0]


# --------------------------------------------------------------- simple gates
def test_strict_improvement():
    g = StrictImprovement()
    assert g.check(S(0.51), S(0.5), CTX)
    assert not g.check(S(0.5), S(0.5), CTX)              # ties are discarded
    assert not g.check(S(0.49), S(0.5), CTX)
    low = StrictImprovement(lower_is_better=True)
    assert low.check(S(0.9), S(1.0), CTX) and not low.check(S(1.0), S(1.0), CTX)
    mg = StrictImprovement(min_gain=0.05)
    assert not mg.check(S(0.55), S(0.5), CTX) and mg.check(S(0.56), S(0.5), CTX)
    v = g.check(S(0.6), S(0.5), CTX)
    assert isinstance(v, Verdict) and v.details["gain"] == pytest.approx(0.1)


def test_float_ties_are_ties():
    """Bug fix: equal per-task scores summed in a different order differ in the last
    bit; gates used to call that a strict gain (or a loss below the floor)."""
    import numpy as np

    thirds_a = [2, 0, 2, 1, 2, 3, 0, 3, 2, 1]                   # per-task successes out of k=3
    thirds_b = [3, 1, 3, 2, 2, 0, 0, 1, 2, 2]                   # the same multiset, reordered
    a = float(np.mean([x / 3 for x in thirds_a]))
    b = float(np.mean([x / 3 for x in thirds_b]))
    assert a != b and abs(a - b) < 1e-12                      # same multiset, different float
    hi, lo = max(a, b), min(a, b)
    assert not StrictImprovement().check(S(hi), S(lo), CTX)
    assert ImprovementOrEqual().check(S(lo), S(hi), CTX)
    assert NoiseFloor().check(S(lo), S(hi), GateContext(delta=0.0))
    assert not CostRule().check(S(hi), S(lo), GateContext(delta=0.0))  # shaped == 0 (tie) is not > 0
    assert not MinGain().check(S(0.55), S(0.5), GateContext(delta=0.05))  # 0.55-0.5 = 0.050000000000000044


def test_numpy_scores_give_plain_bool_verdicts():
    """Bug fix: numpy float scores made Verdict.accept a numpy.bool_, and bool(verdict)
    raised TypeError ("__bool__ should return bool")."""
    import numpy as np

    s = np.array([0.5, 0.6])
    ctx = GateContext(best_score=s[0], delta=np.float64(0.02))
    for g in (StrictImprovement(), ImprovementOrEqual(), MinGain(), NoiseFloor(), CostRule(), RRSIGate(),
              MetricGuard("m", 0.0), DualGate({"score": 0.0}, {"m": 0.0})):
        v = g.check(Scored(s[1], cost=np.float64(10), metrics={"m": s[0]}),
                    Scored(s[0], cost=np.float64(10), metrics={"m": s[1]}), ctx)
        assert type(v.accept) is bool and bool(v) is v.accept, g.name
    assert bool(Verdict(np.bool_(True), "x")) is True


def test_improvement_or_equal_and_min_gain():
    assert ImprovementOrEqual().check(S(0.5), S(0.5), CTX)
    assert not ImprovementOrEqual().check(S(0.49), S(0.5), CTX)
    assert not MinGain().check(S(0.52), S(0.5), CTX)           # gain == delta is not enough
    assert MinGain().check(S(0.5201), S(0.5), CTX)


def test_noise_floor_uses_best_score_so_far():
    g = NoiseFloor()
    ctx = GateContext(best_score=0.60, delta=0.02)
    assert g.check(S(0.58), S(0.55), ctx)                     # exactly at S* - delta
    assert not g.check(S(0.5799), S(0.55), ctx)               # above the incumbent but below the floor
    assert g.check(S(0.54), S(0.55), GateContext(delta=0.02))  # no S*: falls back to incumbent
    assert g.check(S(0.5), S(0.5), GateContext(delta=0.0))
    assert g.check(S(0.58), S(0.55), ctx).details["floor"] == pytest.approx(0.58)


def test_noise_floor_blocks_slow_slide():
    """Many individually within-noise losses cannot walk the incumbent down."""
    g = NoiseFloor()
    s_star, inc = 0.60, 0.60
    accepted = []
    for _ in range(10):
        cand = inc - 0.015                                   # each step is inside the 0.02 band
        if g.check(S(cand), S(inc), GateContext(best_score=s_star, delta=0.02)):
            inc = cand
            accepted.append(cand)
            s_star = max(s_star, inc)
    assert len(accepted) == 1 and inc >= 0.58


# ------------------------------------------------------------------- CostRule
def test_cost_rule_above_band_budget():
    g = CostRule()                                            # beta0=0.10, beta1=40 (rrsi/config.py)
    ctx = GateContext(delta=0.02)
    # dS = 0.05 > delta -> budget 0.10 + 40*0.05 = 2.1 relative tokens
    assert g.check(S(0.55, cost=300.0), S(0.5, cost=100.0), ctx)       # dC = 2.0
    assert g.check(S(0.55, cost=310.0), S(0.5, cost=100.0), ctx)       # dC = 2.1 (boundary, <=)
    v = g.check(S(0.55, cost=320.0), S(0.5, cost=100.0), ctx)          # dC = 2.2
    assert not v and v.details["dC"] == pytest.approx(2.2)


def test_cost_rule_within_band_shaped_rule():
    g = CostRule()                                            # w_s=100, w_c=15, w_n=0.5
    ctx = GateContext(delta=0.02)
    cheaper = g.check(S(0.5, cost=90.0), S(0.5, cost=100.0), ctx)      # 0 - 15*(-0.1) = 1.5
    assert cheaper and cheaper.details["shaped"] == pytest.approx(1.5)
    assert not g.check(S(0.5), S(0.5), ctx)                             # shaped == 0 is not > 0
    assert g.check(S(0.5, novelty=1), S(0.5), ctx)                      # exploring a new component: +0.5
    assert g.check(S(0.49, cost=90.0), S(0.5), ctx)                     # -1 + 1.5 = 0.5
    assert not g.check(S(0.51, cost=110.0), S(0.5), ctx)                # 1 - 1.5 = -0.5
    # dS == delta is still "within the band" (rule uses dS > delta for the budget branch)
    at_band = g.check(S(0.52, cost=200.0), S(0.5), ctx)
    assert "shaped" in at_band.details and not at_band               # 2 - 15 = -13


def test_cost_rule_zero_costs_and_ablation():
    g = CostRule()
    ctx = GateContext(delta=0.02)
    assert g.check(S(0.55, cost=0.0), S(0.5, cost=100.0), ctx).details["dC"] == 0.0
    assert g.check(S(0.55, cost=500.0), S(0.5, cost=0.0), ctx).details["dC"] == 0.0
    strict = CostRule(within_band=False)
    assert not strict.check(S(0.5, cost=10.0), S(0.5), ctx)
    assert strict.check(S(0.6), S(0.5), ctx)
    custom = CostRule(beta0=0.0, beta1=0.0)
    assert not custom.check(S(0.6, cost=101.0), S(0.5), ctx) and custom.check(S(0.6, cost=100.0), S(0.5), ctx)


# ---------------------------------------------------------------- MetricGuard
def test_metric_guard():
    g = MetricGuard("valid_rate", tol=0.05)
    assert g.name == "guard:valid_rate"
    assert g.check(S(0.5, metrics={"valid_rate": 0.95}), S(0.5, metrics={"valid_rate": 1.0}), CTX)
    assert not g.check(S(0.5, metrics={"valid_rate": 0.94}), S(0.5, metrics={"valid_rate": 1.0}), CTX)
    assert g.check(S(0.5), S(0.5, metrics={"valid_rate": 1.0}), CTX)   # missing -> skipped
    lat = MetricGuard("latency", tol=1.0, higher_is_better=False)
    assert lat.check(S(0, metrics={"latency": 11}), S(0, metrics={"latency": 10}), CTX)
    assert not lat.check(S(0, metrics={"latency": 11.5}), S(0, metrics={"latency": 10}), CTX)


# -------------------------------------------------------------------- RRSIGate
def test_rrsi_gate_composition_and_reasons():
    g = RRSIGate(guards=[MetricGuard("valid_rate", 0.0)])
    assert [x.name for x in g.gates] == ["noise_floor", "cost_rule", "guard:valid_rate"]
    ctx = GateContext(best_score=0.7, delta=0.02)
    v = g.check(S(0.6), S(0.5), ctx)
    assert not v and v.reason.startswith("noise_floor")        # floor is checked first
    ok = g.check(S(0.75, metrics={"valid_rate": 1.0}), S(0.7, metrics={"valid_rate": 1.0}), ctx)
    assert ok and set(ok.details) == {"noise_floor", "cost_rule", "guard:valid_rate"}
    v = g.check(S(0.75, metrics={"valid_rate": 0.9}), S(0.7, metrics={"valid_rate": 1.0}), ctx)
    assert not v and v.reason.startswith("guard:valid_rate")
    v = g.check(S(0.75, cost=1000.0), S(0.7), ctx)              # dC = 9 > 0.1 + 40*0.05 = 2.1
    assert not v and v.reason.startswith("cost_rule")


def test_rrsi_gate_ablations():
    no_cost = RRSIGate(cost=False)
    ctx = GateContext(best_score=0.5, delta=0.02)
    assert no_cost.check(S(0.5, cost=1e6), S(0.5), ctx)        # any cost admissible
    no_floor = RRSIGate(floor=False)
    assert [x.name for x in no_floor.gates] == ["cost_rule"]
    assert no_floor.check(S(0.3, cost=50.0), S(0.3), GateContext(best_score=0.9, delta=0.02))
    custom = RRSIGate(cost_rule=CostRule(beta0=0.0, beta1=0.0))
    assert not custom.check(S(0.6, cost=101.0), S(0.5), GateContext(best_score=0.5, delta=0.02))


# ------------------------------------------------------------- BootstrapRigor
def test_bootstrap_rigor():
    g = BootstrapRigor()                                        # p=0.95 over per-seed samples
    assert g.check(S(0.8, samples=[0.8, 0.81, 0.79]), S(0.5, samples=[0.5, 0.51, 0.49]), CTX)
    v = g.check(S(0.52, samples=[0.45, 0.6, 0.51]), S(0.5, samples=[0.48, 0.52, 0.5]), CTX)
    assert not v and 0 < v.details["p_better"] < 0.95
    assert not g.check(S(0.9), S(0.5, samples=[0.5]), CTX)      # no samples -> reject
    bpb = BootstrapRigor(lower_is_better=True)
    assert bpb.check(S(0, samples=[1.00, 1.01, 0.99]), S(0, samples=[1.10, 1.11, 1.09]), CTX)
    assert not bpb.check(S(0, samples=[1.10, 1.11, 1.09]), S(0, samples=[1.00, 1.01, 0.99]), CTX)


# -------------------------------------------------------------------- DualGate
def test_dual_gate():
    g = DualGate(capability={"score": 0.01, "pass_rate": 0.0}, efficiency={"tokens": 0.0, "usd": 0.10})
    base = S(0.80, metrics={"pass_rate": 0.9, "tokens": 1000, "usd": 1.0})
    ok = g.check(S(0.795, metrics={"pass_rate": 0.9, "tokens": 900, "usd": 1.0}), base, CTX)
    assert ok and "tokens" in ok.reason and ok.details["tokens_saving"] == pytest.approx(0.1)
    v = g.check(S(0.78, metrics={"pass_rate": 0.9, "tokens": 500, "usd": 0.5}), base, CTX)
    assert not v and "capability score" in v.reason              # capability beyond tolerance
    v = g.check(S(0.80, metrics={"pass_rate": 0.9, "tokens": 1000, "usd": 0.95}), base, CTX)
    assert not v and v.reason == "no efficiency metric improved"  # 5% < 10% required on usd
    assert g.check(S(0.80, metrics={"pass_rate": 0.9, "tokens": 1000, "usd": 0.85}), base, CTX)
    v = g.check(S(0.80, metrics={"tokens": 1}), base, CTX)
    assert not v and "pass_rate missing" in v.reason
    zero_base = S(0.8, metrics={"pass_rate": 0.9, "tokens": 0, "usd": 0})
    assert not g.check(S(0.8, metrics={"pass_rate": 0.9, "tokens": 0, "usd": 0}), zero_base, CTX)


# ------------------------------------------------------------ AllOf / select
def test_allof_flattening_and_and_operator():
    g = StrictImprovement() & NoiseFloor() & MinGain()
    assert isinstance(g, AllOf) and len(g.gates) == 3
    assert len((RRSIGate() & MetricGuard("m", 0)).gates) == 3
    v = g.check(S(0.6), S(0.5), GateContext(delta=0.02))
    assert v and v.reason == "all gates passed"
    assert bool(Verdict(False, "x")) is False


def test_select_argmax_over_admissible():
    inc = S(0.5)
    cands = [("a", S(0.55)), ("b", S(0.7, cost=1e5)), ("c", S(0.6)), ("d", S(0.4))]
    winner, verdicts = select(cands, inc, RRSIGate(), GateContext(best_score=0.5, delta=0.02))
    assert winner == "c"                                       # b is best but fails the cost rule
    assert [c for c, _ in verdicts] == ["a", "b", "c", "d"]
    assert [bool(v) for _, v in verdicts] == [True, False, True, False]
    w, _ = select(cands, inc, StrictImprovement(), CTX, key=lambda s: -s.cost)
    assert w == "a"                                            # custom key: cheapest admissible
    w, vs = select([("x", S(0.4))], inc, StrictImprovement(), CTX)
    assert w is None and not vs[0][1]
    w, _ = select([("first", S(0.6)), ("second", S(0.6))], inc, StrictImprovement(), CTX)
    assert w == "first"                                        # ties -> earliest candidate
    assert select([], inc, StrictImprovement(), CTX) == (None, [])
