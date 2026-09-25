"""The overview's overfitting-trap toy: bit-exact port of the page's JavaScript.

Reference numbers were produced by running the simulator extracted verbatim from the
overview HTML under Node 22 (``node toy_ref.js <pTrick> <sigma> <catch>``)."""
import pytest

from rsi.rrsi.toy import gauss, mulberry32, simulate

NODE_REF = {  # (p_trick, sigma, catch) -> (plainEv, plainHo, regEv, regHo, accPlain, accReg) at round 60
    (0.35, 0.02, 0.8): (65.36219631880111, 2.8414667002741907, 19.635933049793746, 9.322967069230371, 35.215, 14.505),
    (0.6, 0.04, 0.3): (80.97532887701692, -8.790412361191935, 43.90083175386872, -2.215933092571676, 36.775, 19.825),
    (0.2, 0.035, 0.0): (31.990338205042562, 0.7988530104736977, 27.261091397956207, 3.7886013287877414, 31.175, 20.42),
}
NODE_PLAIN_EV_HEAD = [0, 1.1186621590482395, 2.1912391003236134, 3.4257413893123507, 4.674035415328033]


def test_mulberry32_is_uint32_and_deterministic():
    r1, r2 = mulberry32(7), mulberry32(7)
    xs = [r1() for _ in range(1000)]
    assert xs == [r2() for _ in range(1000)]
    assert all(0.0 <= x < 1.0 for x in xs)
    g = mulberry32(3)
    assert abs(sum(gauss(g) for _ in range(20000)) / 20000) < 0.03


@pytest.mark.parametrize("params", list(NODE_REF))
def test_simulate_matches_node(params):
    r = simulate(*params)
    ref = NODE_REF[params]
    got = (r.plain_ev[-1], r.plain_ho[-1], r.reg_ev[-1], r.reg_ho[-1], r.acc_plain, r.acc_reg)
    for a, b in zip(got[:4], ref[:4]):
        assert a == pytest.approx(b, abs=1e-9)
    assert got[4:] == ref[4:]


def test_default_curve_head_and_verdict():
    r = simulate()
    assert r.plain_ev[:5] == pytest.approx(NODE_PLAIN_EV_HEAD, abs=1e-12)
    assert r.verdict() == "The guarded loop practises worse but transfers better."
    assert len(r.finals["guarded_unseen"]) == 200
    # RRSI's z=2 band is stricter than the demo's half-sd band
    strict = simulate(band_z=2.0)
    assert strict.acc_reg < r.acc_reg
