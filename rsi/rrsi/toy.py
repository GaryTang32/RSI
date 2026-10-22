"""Bit-exact port of the overview page's "overfitting trap" simulator (plain vs guarded keep rule)."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Optional

ROUNDS = 60
RUNS = 200
_M32 = 0xFFFFFFFF


def mulberry32(a: int) -> Callable[[], float]:
    """The demo's ``mulberry32`` generator (uint32 arithmetic)."""
    state = [a & _M32]

    def rnd() -> float:
        s = (state[0] + 0x6D2B79F5) & _M32
        state[0] = s
        t = ((s ^ (s >> 15)) * (1 | s)) & _M32
        t = ((t + (((t ^ (t >> 7)) * (61 | t)) & _M32)) & _M32) ^ t
        return ((t ^ (t >> 14)) & _M32) / 4294967296.0

    return rnd


def gauss(rng: Callable[[], float]) -> float:
    """The demo's Box-Muller (rejects u == 0 and v == 0)."""
    u = v = 0.0
    while u == 0.0:
        u = rng()
    while v == 0.0:
        v = rng()
    return math.sqrt(-2.0 * math.log(u)) * math.cos(2.0 * math.pi * v)


@dataclass
class ToyResult:
    """Run-averaged curves in points (x100) plus per-run finals."""

    plain_ev: list[float]
    plain_ho: list[float]
    reg_ev: list[float]
    reg_ho: list[float]
    acc_plain: float
    acc_reg: float
    finals: dict = field(default_factory=dict)

    def last(self) -> dict:
        return {"plain_practice": self.plain_ev[-1], "plain_unseen": self.plain_ho[-1],
                "guarded_practice": self.reg_ev[-1], "guarded_unseen": self.reg_ho[-1],
                "changes_kept_plain": self.acc_plain, "changes_kept_guarded": self.acc_reg}

    def verdict(self) -> str:
        if self.reg_ho[-1] > self.plain_ho[-1]:
            return "The guarded loop practises worse but transfers better."
        return "At these settings the plain loop transfers as well or better. Try more tricks or more noise."


def simulate(p_trick: float = 0.35, sigma: float = 0.02, catch_rate: float = 0.8, *, rounds: int = ROUNDS,
             runs: int = RUNS, band_z: float = 0.5, critic: bool = True) -> ToyResult:
    """The demo's simulator with its slider defaults; ``critic=False`` still draws the critic stream."""
    plain_ev = [0.0] * (rounds + 1)
    plain_ho = [0.0] * (rounds + 1)
    reg_ev = [0.0] * (rounds + 1)
    reg_ho = [0.0] * (rounds + 1)
    band = band_z * sigma * math.sqrt(2.0)
    acc_plain = acc_reg = 0
    finals: dict[str, list[float]] = {"plain_practice": [], "plain_unseen": [], "guarded_practice": [],
                                      "guarded_unseen": [], "kept_plain": [], "kept_guarded": [],
                                      "tricks_kept_plain": [], "tricks_kept_guarded": []}
    for s in range(1, runs + 1):
        rp, ra = mulberry32(s * 101 + 7), mulberry32(s * 7 + 1)
        rb, rc = mulberry32(s * 13 + 5), mulberry32(s * 29 + 3)
        pe = ph = re_ = rh = 0.0
        kp = kr = tp = tr = 0
        for i in range(rounds):
            trick = rp() < p_trick
            if trick:
                e = 0.03 + 0.01 * gauss(rp)
                g = -0.004 + 0.006 * gauss(rp)
            else:
                e = -0.004 + 0.02 * gauss(rp)
                g = e
            n1 = gauss(ra)
            n2 = gauss(ra)
            if (e + sigma * n1) - (sigma * n2) > 0:
                pe += e
                ph += g
                acc_plain += 1
                kp += 1
                tp += trick
            if not (trick and (rc() < catch_rate) and critic):
                m1 = gauss(rb)
                m2 = gauss(rb)
                if (e + sigma * m1) - (sigma * m2) > band:
                    re_ += e
                    rh += g
                    acc_reg += 1
                    kr += 1
                    tr += trick
            plain_ev[i + 1] += pe
            plain_ho[i + 1] += ph
            reg_ev[i + 1] += re_
            reg_ho[i + 1] += rh
        for key, val in (("plain_practice", pe), ("plain_unseen", ph), ("guarded_practice", re_),
                         ("guarded_unseen", rh)):
            finals[key].append(val * 100.0)
        finals["kept_plain"].append(kp)
        finals["kept_guarded"].append(kr)
        finals["tricks_kept_plain"].append(tp)
        finals["tricks_kept_guarded"].append(tr)

    def avg(a: list[float]) -> list[float]:
        return [(v / runs) * 100.0 for v in a]

    return ToyResult(avg(plain_ev), avg(plain_ho), avg(reg_ev), avg(reg_ho), acc_plain / runs, acc_reg / runs,
                     finals)


def sweep(grid: dict[str, list[float]], base: Optional[dict] = None, **kw) -> list[dict]:
    """One-factor sweeps around ``base`` (defaults to the page's sliders)."""
    base = dict(base or {"p_trick": 0.35, "sigma": 0.02, "catch_rate": 0.8})
    rows = []
    for name, values in grid.items():
        for v in values:
            params = {**base, name: v}
            r = simulate(**params, **kw)
            rows.append({"factor": name, "value": v, **params, **r.last(),
                         "guarded_transfers_better": r.reg_ho[-1] > r.plain_ho[-1]})
    return rows
