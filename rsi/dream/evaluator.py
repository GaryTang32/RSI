"""ReplayEvaluator: score a policy version by replaying it over every past world.

"Each version is scored on every past search" [doc]; ``V^m = (1/t) sum_i V_i^m``
[paper:§3 p.6]. For every world the policy first plans its grid (``plan_grid``
with the live manifests recorded *before* that world and the trace's support
fields), then ``solve`` runs against a :class:`ReplayQuestion` behind the
:class:`PrefixGuard`. The report carries

* ``value`` (the selection objective: Eq. 1 mean, or the Pareto sweep reward),
* per-world values, episode statistics (N, k, batch sizes, attainment ...),
* the beta sweep (``beta_sweep.json``: pareto.reward, AUC, parallel penalty,
  per-beta frontier) when requested,
* the execution traces (``policy_execution_traces.jsonl``: one replay episode per
  (frozen trace, beta), with prefix state, batch and revealed outcomes per round),
* diagnostics for the developer (premature stops, wasted probes, serial batches,
  plan errors, guard violations) and the replay CPU cost.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Optional, Sequence

import numpy as np

from .guard import InProcessRunner, get_runner
from .objectives import EpisodeResult, Eq1Objective, ParetoSweepObjective
from .policy_api import GridPlan, GridPlanningContext
from .question import ReplayQuestion
from .tree import DiscoveryTree


@dataclass
class PolicyReport:
    policy_id: str
    label: str
    objective: str
    value: float
    per_world: list[float]
    episodes: list[EpisodeResult]
    sweep: Optional[dict] = None
    sweep_episodes: list[EpisodeResult] = field(default_factory=list)
    diagnostics: dict = field(default_factory=dict)
    cpu_s: float = 0.0
    wall_s: float = 0.0
    n_episodes: int = 0

    @property
    def disqualified(self) -> int:
        return int(sum(e.disqualified for e in self.episodes))

    def summary(self) -> dict:
        return {"policy": self.policy_id[:10], "label": self.label, "objective": self.objective,
                "value": round(self.value, 6), "per_world": [round(v, 5) for v in self.per_world],
                "sweep_reward": None if self.sweep is None else round(self.sweep["reward"], 6),
                "diagnostics": self.diagnostics, "cpu_s": round(self.cpu_s, 4), "wall_s": round(self.wall_s, 4),
                "n_episodes": self.n_episodes}

    def to_json(self) -> dict:
        return {**self.summary(), "episodes": [e.to_json() for e in self.episodes], "sweep": self.sweep}

    def beta_sweep_json(self) -> dict:
        return self.sweep or {}

    def traces_jsonl(self, max_rows: Optional[int] = None) -> str:
        rows = []
        for e in list(self.episodes) + list(self.sweep_episodes):
            rows.append(json.dumps({"world": e.world_id, "beta": e.beta, "plan": e.plan,
                                    "out_of_support": e.out_of_support, "N": e.N, "k": e.k,
                                    "attainment": round(e.attainment, 4), "error": e.error,
                                    "violations": e.violations, "rounds": e.trace}, default=float))
        if max_rows is not None:
            rows = rows[:max_rows]
        return "\n".join(rows) + ("\n" if rows else "")


def diagnostics(episodes: Sequence[EpisodeResult]) -> dict:
    """Between-round feedback. May use hindsight about the frozen traces (e.g. whether
    the ceiling was reached) because it is read *outside* ``solve()``."""
    ok = [e for e in episodes if not e.disqualified]
    if not episodes:
        return {}
    wasted, serial, late = [], [], []
    for e in ok:
        # probes after the final best was first revealed are "wasted" work
        final_n = next((n for n, b in e.curve if b is not None and e.best is not None and b >= e.best), e.N)
        wasted.append((e.N - final_n) / max(1, e.N))
        serial.append(sum(1 for b in e.batch_sizes if b == 1) / max(1, len(e.batch_sizes)))
    for e in ok:
        late.append(1.0 if e.attainment < 0.999 else 0.0)
    return {
        "attainment": float(np.mean([e.attainment for e in episodes])),
        "probes_frac": float(np.mean([e.probe_frac for e in episodes])),
        "N": float(np.mean([e.N for e in episodes])),
        "k": float(np.mean([e.k for e in episodes])),
        "mean_batch": float(np.mean([e.mean_batch for e in ok])) if ok else 0.0,
        "batch_fill": float(np.mean([e.mean_batch / max(1, e.W) for e in ok])) if ok else 0.0,
        "missed_ceiling_rate": float(np.mean(late)) if late else 1.0,
        "wasted_probe_frac": float(np.mean(wasted)) if wasted else 0.0,
        "serial_round_frac": float(np.mean(serial)) if serial else 0.0,
        "out_of_support_rate": float(np.mean([e.out_of_support for e in episodes])),
        "plan_errors": sorted({e.plan_error for e in episodes if e.plan_error})[:3],
        "disqualified": int(sum(e.disqualified for e in episodes)),
        "violations": sorted({v for e in episodes for v in e.violations})[:3],
        "errors": sorted({str(e.error)[-300:] for e in episodes if e.error})[:2],
        "batch_errors": sorted({b for e in episodes for b in e.batch_errors})[:2],
    }


class ReplayEvaluator:
    """Replays a policy (code) over replay worlds.

    Parameters
    ----------
    objective: :class:`Eq1Objective` (default) or :class:`ParetoSweepObjective` - the
        selection objective. With Eq. 1, ``sweep_grid`` optionally adds a beta sweep
        for feedback (``beta_sweep.json``) without changing the selection value; with
        the Pareto objective every evaluation runs the sweep (it is the value).
    W: workers (max batch size). K2: replay round limit (None = unlimited).
    root_mode / hide_missing: see :class:`ReplayQuestion`.
    runner: ``"subprocess"`` (sandbox, default) / ``"inprocess"`` or a runner object.
    fallback / hard caps: the :class:`GridPlanningContext` fields.
    unguarded: pass the raw question to the policy (E8 ablation only; in-process only).
    """

    def __init__(self, objective=None, *, W: int = 4, K2: Optional[int] = None, root_mode: str = "earliest",
                 hide_missing: bool = False, runner="subprocess", fallback: tuple[int, int] = (4, 4),
                 hard_max: tuple[int, int] = (16, 16), sweep_grid: Optional[Sequence[float]] = None,
                 unguarded: bool = False, trace_rounds: bool = True) -> None:
        self.objective = objective or Eq1Objective()
        self.W, self.K2 = W, K2
        self.root_mode, self.hide_missing = root_mode, hide_missing
        self.runner = get_runner(runner) if isinstance(runner, str) else (runner or InProcessRunner())
        self.fallback, self.hard_max = fallback, hard_max
        self.sweep_grid = tuple(sweep_grid) if sweep_grid else (
            tuple(self.objective.beta_grid) if isinstance(self.objective, ParetoSweepObjective) else ())
        self.unguarded = unguarded
        self.trace_rounds = trace_rounds
        self.episodes_run = 0
        self.cpu_s = 0.0

    # -------------------------------------------------------------------- context
    def context(self, world: DiscoveryTree, manifests: Sequence[dict] = ()) -> GridPlanningContext:
        rnd = world.meta.get("round")
        hist = [m for m in manifests if rnd is None or int(m.get("iteration", 0)) < int(rnd)]
        return GridPlanningContext(history=list(hist), fallback_branch_count=self.fallback[0],
                                   fallback_refine_count=self.fallback[1], hard_max_branch_count=self.hard_max[0],
                                   hard_max_refine_count=self.hard_max[1], worker_cap=self.W,
                                   trace_branch_count=world.trace_branch_count,
                                   trace_refine_count=world.trace_refine_count, mode="replay")

    def validate_plan(self, plan: Optional[GridPlan], err: Optional[str]) -> tuple[GridPlan, Optional[str]]:
        if plan is None:
            return GridPlan(self.fallback[0], self.fallback[1], "runner fallback"), err or "no plan"
        b = min(max(1, plan.branch_count), self.hard_max[0])
        r = min(max(0, plan.refine_count), self.hard_max[1])
        note = None if (b, r) == (plan.branch_count, plan.refine_count) else \
            f"plan {plan.branch_count}x{plan.refine_count} outside hard caps; clamped"
        return GridPlan(b, r, plan.reason), note

    # ------------------------------------------------------------------- episodes
    def run_episode(self, session, world: DiscoveryTree, config: dict, context: GridPlanningContext,
                    beta: Optional[float]) -> EpisodeResult:
        t0 = time.process_time()
        plan, perr = session.plan_grid(config, context)
        plan, perr = self.validate_plan(plan, perr)
        q = ReplayQuestion(world, self.W, plan, K=self.K2, root_mode=self.root_mode, hide_missing=self.hide_missing)
        out = session.solve(config, q, unguarded=self.unguarded)
        st = q.stats()
        cpu = time.process_time() - t0 + (out.cpu_s if not isinstance(self.runner, InProcessRunner) else 0.0)
        self.episodes_run += 1
        self.cpu_s += cpu
        return EpisodeResult(
            world_id=world.world_id, beta=beta, plan=q.plan.to_dict(), requested_plan=plan.to_dict(),
            out_of_support=q.out_of_support, N=st["N"], k=st["k"], batch_sizes=st["batch_sizes"], best=st["best"],
            root=world.root_score, ceiling=world.ceiling, world_size=world.size, W=self.W, curve=st["curve"],
            revealed=st["revealed"], reveal_round=st["reveal_round"], error=out.error, violations=out.violations,
            batch_errors=out.batch_errors, plan_error=perr, cpu_s=cpu,
            trace=q.round_log if self.trace_rounds else [])

    def evaluate(self, code: str, worlds: Sequence[DiscoveryTree], *, config: Optional[dict] = None,
                 manifests: Sequence[dict] = (), sweep: Optional[bool] = None, label: str = "",
                 policy_id: str = "") -> PolicyReport:
        """Replay ``code`` over ``worlds``. ``config`` is the policy config (``{"beta": ...}``;
        empty = the policy's baked-in default). ``sweep`` adds the beta sweep (default:
        only when the objective is the Pareto sweep or ``sweep_grid`` was given)."""
        t_wall = time.time()
        cpu0 = self.cpu_s
        config = dict(config or {})
        if isinstance(self.objective, ParetoSweepObjective):
            do_sweep = True            # the sweep IS the selection objective: never skip it
        else:
            do_sweep = bool(self.sweep_grid) if sweep is None else (sweep and bool(self.sweep_grid))
        with self.runner.session(code) as sess:
            ctxs = [self.context(w, manifests) for w in worlds]
            eps = [self.run_episode(sess, w, config, c, config.get("beta")) for w, c in zip(worlds, ctxs)]
            by_beta: dict[float, list[EpisodeResult]] = {}
            sweep_eps: list[EpisodeResult] = []
            if do_sweep:
                for b in self.sweep_grid:
                    cfg = {**config, "beta": float(b)}
                    by_beta[float(b)] = [self.run_episode(sess, w, cfg, c, float(b)) for w, c in zip(worlds, ctxs)]
                    sweep_eps.extend(by_beta[float(b)])
        sweep_res = None
        if do_sweep:
            obj = self.objective if isinstance(self.objective, ParetoSweepObjective) else \
                ParetoSweepObjective(beta_grid=self.sweep_grid)
            sweep_res = obj.sweep(by_beta)
            eq1 = self.objective if isinstance(self.objective, Eq1Objective) else Eq1Objective()
            for p in sweep_res["points"]:
                p["V_eq1"] = eq1.score(by_beta[p["beta"]])
        if isinstance(self.objective, ParetoSweepObjective):
            value = sweep_res["reward"] if sweep_res else float("-inf")
            eq1 = Eq1Objective()
            per_world = [eq1.score_episode(e) for e in eps]
        else:
            per_world = [self.objective.score_episode(e) for e in eps]
            value = float(np.mean(per_world)) if per_world else float("-inf")
        return PolicyReport(policy_id=policy_id, label=label, objective=self.objective.name, value=float(value),
                            per_world=[float(v) for v in per_world], episodes=eps, sweep=sweep_res,
                            sweep_episodes=sweep_eps, diagnostics=diagnostics(eps), cpu_s=self.cpu_s - cpu0,
                            wall_s=time.time() - t_wall, n_episodes=len(eps) + len(sweep_eps))
