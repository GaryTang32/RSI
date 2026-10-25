"""Replay objectives: paper v2 Eq. 1 (default), v1's Eq. 1 per episode, and the Listing-2 pooled sweep reward."""
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
        den = self.ceiling - self.root
        if self.disqualified or self.best is None or den <= 0:
            return 0.0
        return float(min(1.0, max(0.0, (self.best - self.root) / den)))

    @property
    def probe_frac(self) -> float:
        return 1.0 if self.disqualified else self.N / max(1, self.world_size)

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
    """v1's Eq. 1: ``V_i = max_revealed s - beta1 * N + beta2 * N / max(1, k)`` [paper-v1:§3]; ``normalize``: per world."""

    beta1: float = 0.01
    beta2: float = 0.005
    normalize: bool = True
    support: str = "no_reward"  # out-of-support plans "cannot earn replay reward" (L2) | "clip" to the support
    disqualified_margin: float = 1.0
    name: str = "eq1"

    def floor(self, ep: EpisodeResult) -> float:
        return 0.0 if self.normalize else float(ep.root)

    def quality(self, ep: EpisodeResult) -> float:
        if self.support == "no_reward" and ep.out_of_support:
            return self.floor(ep)
        return ep.attainment if self.normalize else float(ep.best if ep.best is not None else ep.root)

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
    """``pareto.reward = pareto.auc - lambda * parallel_penalty`` over a beta sweep [paper:App.B.2 L2:12-22]."""

    beta_grid: tuple = (0.2, 0.4, 0.6, 0.8, 1.0)
    lam: float = 0.1
    name: str = "pareto"
    support: str = "no_reward"
    # "shift": (best - root) / (ceiling - root) [inferred]; "ratio": clip(S / G, 0, 1), the unpublished method draft
    attainment_mode: str = "shift"

    def attainment(self, e: EpisodeResult) -> float:
        if self.support == "no_reward" and e.out_of_support:
            return 0.0
        if self.attainment_mode == "shift":
            return e.attainment
        if self.attainment_mode != "ratio":
            raise ValueError(f"unknown attainment_mode {self.attainment_mode!r} (use 'shift' or 'ratio')")
        if e.disqualified or e.best is None:
            return 0.0
        if not e.ceiling > 0:
            raise ValueError("attainment_mode='ratio' (S/G) needs positive trace scores; this trace's best is "
                             f"{e.ceiling!r}")
        return float(min(1.0, max(0.0, e.best / e.ceiling)))

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
                        "parallel_penalty": float(np.mean(pen)), "disqualified": int(sum(e.disqualified for e in eps))})
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
                f"{list(self.beta_grid)}" + (" (attainment = clip(S/G, 0, 1))" if self.attainment_mode == "ratio"
                                             else ""))


@dataclass
class HypervolumeObjective(ParetoSweepObjective):
    """Paper v2 Eq. 1 [paper:§3]: per world, the normalised hypervolume of the non-dominated (calls n, attainment a)
    points over the rho sweep, minus ``lam`` * mean over rho of k*/n (zero-call replays add 0); V = mean over worlds.

    The paper gives no lambda; ``attainment_mode="shift"`` maps the paper's root score s_r = 0 to the seed's score
    (``"ratio"`` is the literal clip(s / S_i) and needs positive scores). A disqualified world scores below every
    honest one.
    """

    beta_grid: tuple = (0.0, 0.25, 0.5, 0.75, 1.0)
    name: str = "v2"
    disqualified_margin: float = 1.0

    def world_value(self, eps: Sequence[EpisodeResult]) -> float:
        if any(e.disqualified for e in eps):
            return -self.lam - self.disqualified_margin
        pts = [{"probes_frac": min(1.0, e.N / max(1, e.world_size)), "attainment": self.attainment(e), "beta": e.beta}
               for e in eps]
        auc, _ = self.frontier_auc(pts)
        penalty = sum(e.eff_rounds / e.N for e in eps if e.N > 0) / max(1, len(eps))
        return auc - self.lam * penalty

    def sweep(self, by_beta: dict[float, Sequence[EpisodeResult]]) -> dict:
        res = super().sweep(by_beta)   # pooled points and frontier, kept for reports and the default-beta rule
        betas = [b for b in sorted(by_beta) if by_beta[b]]
        n = min(len(by_beta[b]) for b in betas) if betas else 0
        per = [self.world_value([by_beta[b][i] for b in betas]) for i in range(n)]
        res.update(reward=float(np.mean(per)) if per else float("-inf"), per_world=per, objective=self.name)
        return res

    def describe(self) -> str:
        return (f"v2 Eq. 1: per world, hypervolume of (calls, attainment) over rho {list(self.beta_grid)} "
                f"- {self.lam:g} * mean k*/n; V = mean over all replay worlds")
