"""Replay objectives: how one replay episode (and a beta sweep) is scored.

* :class:`EpisodeResult` - what one replay episode produced (authoritative stats
  from the question, never from the policy).
* :class:`Eq1Objective` - paper Eq. 1 [paper:§3 p.6]::

      V_i = max_{v in revealed} s_v  -  beta1 * N  +  beta2 * N / max(1, k)

  with the max including the root. ``normalize=True`` (framework default, spec §9.3)
  first maps scores to ``(s - s_r) / (ceiling_i - s_r)`` so worlds of different
  scales average fairly; ``normalize=False`` uses raw scores (the paper's raw mean,
  and the overview demo, whose root counts as 0). ``support="no_reward"`` gives an
  out-of-support plan no quality credit (Listing 2's "cannot earn replay reward");
  ``"clip"`` (default) just clips the plan to the trace.
* :class:`ParetoSweepObjective` - the Listing-2 objective [paper:App.B.2 L2:12-22]::

      pareto.reward = pareto.auc - lambda * parallel_penalty

  ``auc``: area under the attainment-vs-probe-fraction frontier traced by the beta
  sweep (attainment = normalized best per trace; probes normalized by the trace size);
  ``parallel_penalty``: mean over the sweep of effective_sequential_rounds / probes,
  where a batch of k cells costs ceil(k / W) sequential rounds. The exact AUC
  definition and lambda are not reported in the paper - this is [inferred].
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Optional, Sequence

import numpy as np


@dataclass
class EpisodeResult:
    world_id: str
    beta: Optional[float]
    plan: dict
    requested_plan: dict
    out_of_support: bool
    N: int
    k: int
    batch_sizes: list[int]
    best: Optional[float]
    root: float
    ceiling: float
    world_size: int
    W: int
    curve: list = field(default_factory=list)
    revealed: list[str] = field(default_factory=list)
    reveal_round: dict = field(default_factory=dict)
    error: Optional[str] = None
    violations: list[str] = field(default_factory=list)
    batch_errors: list[str] = field(default_factory=list)
    plan_error: Optional[str] = None
    cpu_s: float = 0.0
    trace: list[dict] = field(default_factory=list)

    @property
    def disqualified(self) -> bool:
        return bool(self.error or self.violations or self.batch_errors)

    @property
    def attainment(self) -> float:
        """Normalized best in [0, 1]: (best - root) / (ceiling - root)."""
        if self.disqualified or self.best is None:
            return 0.0
        den = self.ceiling - self.root
        if den <= 0:
            return 0.0
        return float(min(1.0, max(0.0, (self.best - self.root) / den)))

    @property
    def probe_frac(self) -> float:
        if self.disqualified:
            return 1.0
        return self.N / max(1, self.world_size)

    @property
    def eff_rounds(self) -> int:
        return int(sum(math.ceil(b / max(1, self.W)) for b in self.batch_sizes))

    @property
    def mean_batch(self) -> float:
        return self.N / max(1, self.k)

    def to_json(self, with_trace: bool = False) -> dict:
        d = asdict(self)
        if not with_trace:
            d.pop("trace", None)
        d.update({"attainment": self.attainment, "disqualified": self.disqualified, "probe_frac": self.probe_frac})
        return d


@dataclass
class Eq1Objective:
    """Paper Eq. 1. A disqualified episode (guard violation, illegal batch, policy crash)
    scores strictly below every honest episode on the same world: the quality floor (0
    normalized, ``s_r`` raw) minus the cost of revealing the whole world minus
    ``disqualified_margin``. A fixed constant such as -1 is not enough: on a paper-scale
    world (32 x 20 = 640 cells, beta1 = 0.01) an honest full-grid policy scores about -5.2,
    so a crashing or cheating policy would outrank it; with raw negative scores (e.g. a
    negated loss) the same happens on small worlds."""

    beta1: float = 0.01
    beta2: float = 0.005
    normalize: bool = True
    # out-of-support plans (beyond the frozen trace's width/depth): "no_reward" (default) = Listing 2's
    # "cannot earn replay reward" (quality at the floor, probes still cost); "clip" = the plan is
    # intersected with the support and scored as if it had asked for less (non-default: E2 shows it
    # over-estimates such plans by +0.14)
    support: str = "no_reward"
    disqualified_margin: float = 1.0
    name: str = "eq1"

    def floor(self, ep: EpisodeResult) -> float:
        """Lowest quality term an honest episode can get (the root: nothing revealed)."""
        return 0.0 if self.normalize else float(ep.root)

    def quality(self, ep: EpisodeResult) -> float:
        if self.support == "no_reward" and ep.out_of_support:
            return self.floor(ep)
        if self.normalize:
            return ep.attainment
        return float(ep.best if ep.best is not None else ep.root)

    def disqualified_value(self, ep: EpisodeResult) -> float:
        return self.floor(ep) - abs(self.beta1) * max(ep.world_size, ep.N) - self.disqualified_margin

    def score_episode(self, ep: EpisodeResult) -> float:
        if ep.disqualified:
            return self.disqualified_value(ep)
        return self.quality(ep) - self.beta1 * ep.N + self.beta2 * ep.N / max(1, ep.k)

    def score(self, episodes: Sequence[EpisodeResult]) -> float:
        return float(np.mean([self.score_episode(e) for e in episodes])) if episodes else float("-inf")

    def describe(self) -> str:
        norm = "normalized per world" if self.normalize else "raw scores"
        return (f"Eq.1 replay score V = best - {self.beta1:g}*N + {self.beta2:g}*N/max(1,k) ({norm}); "
                f"V of the policy = mean over all replay worlds")


@dataclass
class ParetoSweepObjective:
    beta_grid: tuple = (0.2, 0.4, 0.6, 0.8, 1.0)
    lam: float = 0.1
    name: str = "pareto"
    #: "no_reward" (default, Listing 2): an out-of-support episode attains 0; "clip": plan ∩ support
    support: str = "no_reward"

    def attainment(self, e: EpisodeResult) -> float:
        return 0.0 if (self.support == "no_reward" and e.out_of_support) else e.attainment

    def sweep(self, by_beta: dict[float, Sequence[EpisodeResult]]) -> dict:
        pts = []
        for b in sorted(by_beta):
            eps = list(by_beta[b])
            if not eps:
                continue
            pen = [(e.eff_rounds / e.N) if (e.N > 0 and not e.disqualified) else 1.0 for e in eps]
            pts.append({"beta": b, "probes_frac": float(np.mean([e.probe_frac for e in eps])),
                        "attainment": float(np.mean([self.attainment(e) for e in eps])),
                        "N": float(np.mean([e.N for e in eps])), "k": float(np.mean([e.k for e in eps])),
                        "mean_batch": float(np.mean([e.mean_batch for e in eps])),
                        "parallel_penalty": float(np.mean(pen)),
                        "disqualified": int(sum(e.disqualified for e in eps))})
        auc, frontier = self.frontier_auc(pts)
        penalty = float(np.mean([p["parallel_penalty"] for p in pts])) if pts else 1.0
        att = [p["attainment"] for p in pts]
        work = [p["probes_frac"] for p in pts]
        degenerate = len(pts) < 2 or (max(att) - min(att) < 1e-9 and max(work) - min(work) < 1e-9)
        return {"reward": auc - self.lam * penalty, "auc": auc, "parallel_penalty": penalty, "lambda": self.lam,
                "points": pts, "frontier": frontier, "degenerate": bool(degenerate)}

    @staticmethod
    def frontier_auc(points: Sequence[dict]) -> tuple[float, list[dict]]:
        """f(x) = best attainment reachable with probe fraction <= x; AUC = integral_0^1 f."""
        pts = sorted(points, key=lambda p: (p["probes_frac"], -p["attainment"]))
        frontier, best = [], -1.0
        for p in pts:
            if p["attainment"] > best:
                best = p["attainment"]
                frontier.append({"probes_frac": min(1.0, p["probes_frac"]), "attainment": best, "beta": p["beta"]})
        auc = 0.0
        for i, f in enumerate(frontier):
            x0 = f["probes_frac"]
            x1 = frontier[i + 1]["probes_frac"] if i + 1 < len(frontier) else 1.0
            auc += f["attainment"] * max(0.0, x1 - x0)
        return float(auc), frontier

    def score(self, by_beta: dict[float, Sequence[EpisodeResult]]) -> float:
        return float(self.sweep(by_beta)["reward"])

    def describe(self) -> str:
        return (f"pareto.reward = pareto.auc - {self.lam:g} * parallel_penalty over the beta grid "
                f"{list(self.beta_grid)}")
