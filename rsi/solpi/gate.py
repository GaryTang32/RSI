"""SoL-Pi acceptance: a predeclared dual gate (capability floor AND efficiency gain) and a one-way held-out firewall."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Optional, Sequence

import numpy as np

from ..core.evaluate import EvalResult, Evaluator
from ..core.gates import DualGate as CoreDualGate
from ..core.gates import Scored

SOLVED_EPS = 1e-9
METRICS = ("score", "solved", "tokens", "cost", "steps")


@dataclass(frozen=True)
class GateSpec:
    """Predeclared gate. Modes: aggregate | per_family (floor in every family) | efficiency_only | eta_better."""

    capability: tuple[tuple[str, float], ...] = (("score", 0.02), ("solved", 0.02))
    efficiency: tuple[str, ...] = ("tokens", "cost")
    min_gain: float = 0.02                       # min relative improvement of an efficiency metric
    mode: str = "aggregate"
    tolerance_kind: str = "relative"             # relative | absolute
    min_improved_frac: float = 0.0               # per_family: share of families whose efficiency must improve
    family_regression_tol: float = 0.05          # per_family: max relative efficiency regression in any family
    families: Optional[tuple[str, ...]] = None   # restrict the screen to these families

    def digest(self) -> str:
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()[:16]


@dataclass
class Metrics:
    """Mean per-trial metrics, aggregate and per family."""

    agg: dict[str, float]
    fam: dict[str, dict[str, float]]
    n: int = 0

    def to_json(self) -> dict:
        return {"agg": self.agg, "fam": self.fam, "n": self.n}


def metrics_from_eval(ev: EvalResult, families: Optional[Iterable[str]] = None) -> Metrics:
    fams = set(families) if families else None
    rows: dict[str, list[dict]] = {}
    allrows = []
    for trs in ev.trials.values():
        for t in trs:
            if fams is not None and t.family not in fams:
                continue
            r = {"score": t.score, "solved": float(t.score >= 1.0 - SOLVED_EPS), "tokens": float(t.tokens),
                 "cost": float(t.cost_usd), "steps": float(t.steps)}
            rows.setdefault(t.family, []).append(r)
            allrows.append(r)

    def mean(rs: list[dict]) -> dict[str, float]:
        if not rs:
            return {k: float("nan") for k in (*METRICS, "eta")}
        m = {k: float(np.mean([r[k] for r in rs])) for k in METRICS}
        m["eta"] = m["cost"] / m["score"] if m["score"] > 0 else float("inf")
        return m

    return Metrics(mean(allrows), {f: mean(rs) for f, rs in rows.items()}, len(allrows))


@dataclass
class GateResult:
    accept: bool
    reason: str
    capability: dict = field(default_factory=dict)
    efficiency: dict = field(default_factory=dict)
    per_family: dict = field(default_factory=dict)
    spec_digest: str = ""

    def to_json(self) -> dict:
        return asdict(self)


def _within(base: float, cand: float, tol: float, kind: str) -> bool:
    return cand >= (base * (1 - tol) if kind == "relative" else base - tol) - 1e-12


def _saving(base: float, cand: float) -> float:
    return (base - cand) / base if base and base > 0 and math.isfinite(base) else 0.0


class DualGate:
    """accept iff every capability metric is within tolerance of the base AND some efficiency metric improves."""

    def __init__(self, spec: GateSpec = GateSpec()) -> None:
        self.spec = spec
        self.digest = spec.digest()

    def _cap(self, b: dict, c: dict) -> tuple[bool, dict]:
        out, ok = {}, True
        for m, tol in self.spec.capability:
            if m not in b or m not in c:        # an unmeasured predeclared metric fails closed
                out[m] = {"base": b.get(m), "cand": c.get(m), "tol": tol, "pass": False, "missing": True}
                ok = False
                continue
            p = _within(b[m], c[m], tol, self.spec.tolerance_kind)
            out[m] = {"base": b[m], "cand": c[m], "tol": tol, "pass": p}
            ok &= p
        return ok, out

    def _eff(self, b: dict, c: dict) -> tuple[bool, dict]:
        out, any_ok = {}, False
        for m in self.spec.efficiency:
            s = _saving(b[m], c[m])
            imp = s > self.spec.min_gain
            out[m] = {"base": b[m], "cand": c[m], "saving": s, "improved": imp}
            any_ok |= imp
        return any_ok, out

    def accept(self, base: Metrics, cand: Metrics) -> GateResult:
        s = self.spec
        if s.mode == "eta_better":
            sv = _saving(base.agg["eta"], cand.agg["eta"])
            ok = sv > s.min_gain
            return GateResult(ok, f"eta saving {sv:+.3f}", efficiency={"eta": {"base": base.agg["eta"],
                                                                              "cand": cand.agg["eta"], "saving": sv}},
                              spec_digest=self.digest)
        eff_ok, eff = self._eff(base.agg, cand.agg)
        if s.mode == "efficiency_only":
            return GateResult(eff_ok, "efficiency improved" if eff_ok else "no efficiency gain", efficiency=eff,
                              spec_digest=self.digest)
        cap_ok, cap = self._cap(base.agg, cand.agg)
        if s.mode == "aggregate":
            ok = cap_ok and eff_ok
            reason = ("accepted" if ok else "capability below floor" if not cap_ok else "no efficiency gain")
            return GateResult(ok, reason, cap, eff, spec_digest=self.digest)
        if s.mode == "per_family":
            fams = sorted(set(base.fam) & set(cand.fam))
            pf, cap_all, n_imp, regress = {}, True, 0, []
            for f in fams:
                c_ok, c = self._cap(base.fam[f], cand.fam[f])
                e_ok, e = self._eff(base.fam[f], cand.fam[f])
                worse = [m for m, v in e.items() if eff.get(m, {}).get("improved") and
                         v["saving"] < -s.family_regression_tol]
                pf[f] = {"capability": c, "efficiency": e, "cap_ok": c_ok, "eff_ok": e_ok, "regressed": worse}
                cap_all &= c_ok
                n_imp += e_ok
                if worse:
                    regress.append(f)
            need = math.ceil(s.min_improved_frac * len(fams)) if fams else 0
            ok = cap_all and eff_ok and not regress and n_imp >= need
            reason = ("accepted" if ok else "capability below floor in some family" if not cap_all else
                      "no aggregate efficiency gain" if not eff_ok else f"efficiency regressed in {regress}" if regress
                      else f"efficiency improved in only {n_imp}/{len(fams)} families")
            return GateResult(ok, reason, cap, eff, pf, self.digest)
        raise ValueError(f"unknown gate mode {s.mode!r}")

    def as_core_gate(self) -> CoreDualGate:
        """The :class:`rsi.core.gates.DualGate` equivalent (aggregate mode, absolute tolerance)."""
        return CoreDualGate({m: t for m, t in self.spec.capability},
                            {m: self.spec.min_gain for m in self.spec.efficiency})

    @staticmethod
    def scored(m: Metrics) -> Scored:
        return Scored(score=m.agg["score"], cost=m.agg["tokens"], metrics=dict(m.agg))


def nondominated(items: Sequence[tuple[object, Metrics]], efficiency: Sequence[str] = ("tokens", "cost")) -> list:
    """Items not dominated on (score up, every efficiency metric down)."""
    def dom(a: Metrics, b: Metrics) -> bool:
        ge = a.agg["score"] >= b.agg["score"] and all(a.agg[m] <= b.agg[m] for m in efficiency)
        gt = a.agg["score"] > b.agg["score"] or any(a.agg[m] < b.agg[m] for m in efficiency)
        return ge and gt
    return [x for x, mx in items if not any(dom(my, mx) for y, my in items if y is not x)]


@dataclass
class HeldoutResult:
    candidate: str
    artifact_id: str
    passed: bool
    reason: str
    metrics: dict
    base_metrics: dict


class HoldoutFirewall:
    """One-way held-out check of frozen candidates: the driver gets a bool, full results go to a private sink."""

    def __init__(self, domain, llm, gate: DualGate, base_artifact, *, split: str = "holdout", k: int = 1,
                 sink_dir: Optional[str | Path] = None, workers: int = 1) -> None:
        self._gate = gate
        self._split = split
        self._k = k
        self._ev = Evaluator(domain, llm, workers=workers, allow_sealed=True)
        self._base = base_artifact
        self._base_metrics: Optional[Metrics] = None
        self._sink = Path(sink_dir) / "heldout.jsonl" if sink_dir else None
        self._results: list[HeldoutResult] = []
        self._seen: set[str] = set()

    def _metrics(self, art) -> Metrics:
        return metrics_from_eval(self._ev.evaluate(art, self._split, k=self._k))

    def evaluate_frozen(self, name: str, artifact) -> bool:
        if artifact.id in self._seen:
            raise RuntimeError("a frozen candidate is evaluated on the held-out split exactly once")
        self._seen.add(artifact.id)
        if self._base_metrics is None:
            self._base_metrics = self._metrics(self._base)
        m = self._metrics(artifact)
        g = self._gate.accept(self._base_metrics, m)
        r = HeldoutResult(name, artifact.id, g.accept, g.reason, m.to_json(), self._base_metrics.to_json())
        self._results.append(r)
        if self._sink:
            self._sink.parent.mkdir(parents=True, exist_ok=True)
            with self._sink.open("a") as f:
                f.write(json.dumps(asdict(r), default=float) + "\n")
        return g.accept

    def final_report(self) -> list[dict]:
        return [asdict(r) for r in self._results]
