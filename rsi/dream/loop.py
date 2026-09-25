"""DreamRSILoop: online explore -> append world -> dream (M versions) -> select -> deploy.

Paper §3.2 outer loop [paper:§3 p.4-6, Fig.1]::

    for t in 1..T:
        plan  <- pi.plan_grid(live manifests)                 # pre-episode width x depth
        T_t   <- OnlineRollout(pi, plan, W, K1)               # stochastic, costly (agent calls)
        H     <- H + {T_t};  write trace_pool/iter<t>/{tree.json, live_cycle_manifest.json}
        versions <- [pi];  V <- [ReplayScore(pi, H)]           # pi_t^0 = pi_t
        repeat M-1 times: pi' <- developer.revise(feedback);  V += ReplayScore(pi', H)
        pi <- versions[argmax V]                              # incumbent included

Everything is logged: ``discovery.jsonl`` (every attempt of every live search, an
:class:`rsi.core.Ledger` tree), ``policies.jsonl`` (policy lineage with replay values),
``history/r####_<label>/`` (code, report, ``proposal_results/beta_sweep.json``,
``proposal_results/policy_execution_traces.jsonl``), ``trace_pool/iter<t>/`` and
``snapshots/`` (every workspace). ``dream=False`` is Recursive Fixed Exploration;
``guidance=True`` injects written advice summarised from history (E5).
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from ..core.artifact import Artifact
from ..core.ledger import Ledger, Node
from ..core.run import ImprovementResult
from .agent import AttemptContext, DirectionProvider, DiscoveryTask, EvalOutcome, history_records, record_of
from .baselines import Guidance, MockGuidanceSummarizer, guidance_json
from .cost import CostMeter, llm_usage
from .developer import DevContext, ParametricMutator, VersionRecord, default_beta_of
from .evaluator import PolicyReport, ReplayEvaluator, diagnostics
from .guard import get_runner, static_check
from .objectives import Eq1Objective, ParetoSweepObjective
from .policy import code_of, template_code
from .policy_api import GridPlan, GridPlanningContext
from .question import OnlineQuestion, program_only
from .selection import GuardedSelector, Selector
from .tree import ROOT_ID, DiscoveryTree, SnapshotStore


@dataclass
class Config:
    """Dream-RSI hyperparameters. The paper reports none of M, K1, K2, beta1, beta2,
    lambda or the beta grid; defaults follow the spec's §9.3 suggestions [inferred]."""

    rounds: int = 5                        # T: live searches (Lasso 5, math 10 in the paper)
    W: int = 4                             # parallel workers = max batch size
    branch_count: int = 4                  # fallback grid width (pi_1 plan): branches 0..W-1
    refine_count: int = 4                  # fallback depth: attempts 0..refine_count per branch
    hard_max_branch: int = 12
    hard_max_refine: int = 12
    K1: Optional[int] = None               # online round cap (None: bounded by the grid)
    K2: Optional[int] = None               # replay round cap (None: the paper's budget=None)
    M: int = 4                             # policy versions per dreaming phase (incumbent included)
    m_semantics: str = "versions"          # "versions": M-1 revisions | "revisions": M revisions (M+1 candidates)
    objective: str = "eq1"                 # "eq1" (paper Eq.1) | "pareto" (Listing-2 beta-sweep reward)
    beta1: float = 0.01
    beta2: float = 0.005
    normalize: bool = True                 # per-world score normalization before Eq.1
    support: str = "clip"                  # out-of-support plans: "clip" | "no_reward"
    lam: float = 0.1
    beta_grid: tuple = (0.2, 0.4, 0.6, 0.8, 1.0)
    sweep: bool = True                     # beta sweep of the deployed version (feedback + default-beta rule)
    root_mode: str = "earliest"            # replay root semantics: "earliest" (paper §3) | "addressable"
    hide_missing: bool = False
    selector: str = "argmax"               # "argmax" (paper) | "guarded" (held-out worlds + noise margin)
    include_incumbent: bool = True
    holdout_frac: float = 0.34
    noise_z: float = 2.0
    margin_floor: float = 0.01
    sandbox: str = "subprocess"            # where policy code runs: "subprocess" | "inprocess"
    policy_timeout_s: float = 30.0
    root: str = "best"                     # live root of round t: "best" program so far | "seed"
    dream: bool = True                     # False = Recursive Fixed Exploration
    dream_last: bool = False               # also dream after the final live search
    guidance: bool = False                 # E5: history summarised into written advice
    guidance_strength: float = 0.8
    max_calls: Optional[int] = None        # total discovery-agent call budget across rounds
    agent_workers: Optional[int] = None    # threads for concurrent agent calls (default W)
    leakage_check: bool = True
    seed: int = 0

    @property
    def n_revisions(self) -> int:
        return self.M if self.m_semantics == "revisions" else max(0, self.M - 1)


def live_manifest(t: int, tree: DiscoveryTree, q: OnlineQuestion, plan: GridPlan, requested: GridPlan,
                  final_best: float, beta: Optional[float], policy_id: str, calls: int) -> dict:
    """``live_cycle_manifest.json``: the per-iteration facts ``plan_grid`` and the
    default-beta rule may read (planned / effective grid, opened width and depth, probe
    work, decision rounds, scores, beta) [paper:App.B.2 L2:169-175, L2:214-218]."""
    root = tree.root_score
    succ = [n for n in tree.non_root() if n.success and n.score is not None]
    round_best = max([n.score for n in succ] + [root])
    gain = round_best - root
    early = max([n.score for n in succ if n.attempt <= 1] + [root]) - root
    fails = [n for n in tree.non_root() if not n.success]
    hard = [n for n in fails if n.fail_class in ("env_error", "dependency", "hard")]
    # opened width / depth = what the rollout actually used (NOT the support fields, which
    # include the plan's unopened roots and unexplored depth)
    opened = len(tree.branches())
    depth = max((n.attempt for n in tree.non_root()), default=-1)
    return {
        "iteration": t, "planned_grid": requested.to_dict(), "effective_grid": plan.to_dict(),
        "opened_width": opened, "max_depth": depth, "probe_work": q.N,
        "decision_rounds": q.k, "batch_sizes": list(q.batch_sizes), "root_score": root, "round_best": round_best,
        "final_best": final_best, "beta": beta, "gain_early": (early / gain) if gain > 0 else 0.0,
        "gain_late": (1.0 - early / gain) if gain > 0 else 0.0, "fail_frac": len(fails) / max(1, tree.size),
        "hard_fail_frac": len(hard) / max(1, tree.size), "policy": policy_id[:12], "agent_calls": calls,
        "plan_reason": plan.reason,
    }


class DreamRSILoop:
    """One Dream-RSI (or Recursive Fixed Exploration) run on a :class:`DiscoveryTask`."""

    def __init__(self, task: DiscoveryTask, agent, *, config: Optional[Config] = None, developer=None,
                 initial_policy: Optional[str | Artifact] = None, summarizer=None, out_dir: Optional[str] = None,
                 seed_artifact: Optional[Artifact] = None, llms: tuple = (), method: Optional[str] = None) -> None:
        self.task, self.agent = task, agent
        self.cfg = config or Config()
        self.developer = developer or ParametricMutator()
        self.initial_code = code_of(initial_policy) if initial_policy is not None else template_code("parallel_refine")
        self.baseline_code = template_code("parallel_refine")
        self.summarizer = summarizer or MockGuidanceSummarizer()
        self.out = Path(out_dir) if out_dir else None
        self.seed_artifact = seed_artifact or task.seed_artifact()
        # one entry per distinct backend: the same LLM object passed for two roles (e.g. agent and
        # developer) keeps per-role usage in ONE meter, which must not be summed twice
        self.llms = tuple({id(l): l for l in llms if l is not None}.values())
        self.method = method or ("dream-rsi" if self.cfg.dream else "fixed") + ("+guidance" if self.cfg.guidance else "")
        c = self.cfg
        self.meter = CostMeter()
        self.store = SnapshotStore(self.out / "snapshots" if self.out else None)
        self.runner = get_runner(c.sandbox, timeout_s=c.policy_timeout_s) if c.sandbox == "subprocess" \
            else get_runner("inprocess")
        if c.objective == "pareto":
            objective = ParetoSweepObjective(beta_grid=tuple(c.beta_grid), lam=c.lam)
        else:
            objective = Eq1Objective(c.beta1, c.beta2, c.normalize, c.support)
        self.replay = ReplayEvaluator(objective, W=c.W, K2=c.K2, root_mode=c.root_mode, hide_missing=c.hide_missing,
                                      runner=self.runner, fallback=(c.branch_count, c.refine_count),
                                      hard_max=(c.hard_max_branch, c.hard_max_refine),
                                      sweep_grid=tuple(c.beta_grid))
        self.selector = GuardedSelector(c.holdout_frac, 1, c.noise_z, c.margin_floor, c.include_incumbent) \
            if c.selector == "guarded" else Selector(c.include_incumbent)
        self.provider = DirectionProvider(task.directions(), seed=c.seed)
        self.discovery_ledger = Ledger(self.out / "discovery.jsonl" if self.out else None)
        self.policy_ledger = Ledger(self.out / "policies.jsonl" if self.out else None)
        self.worlds: list[DiscoveryTree] = []
        self.manifests: list[dict] = []
        self.history: list[VersionRecord] = []
        self.trajectory: list[dict] = []
        self.rev_counter = 0
        self.guidance: Optional[Guidance] = None

    # ------------------------------------------------------------------- helpers
    def _save(self, rel: str, text: str) -> None:
        if self.out is None:
            return
        p = self.out / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)

    def _policy_node(self, rec: VersionRecord, status: str, parent_node: Optional[str]) -> str:
        nid = f"r{rec.index:04d}"
        if nid in {n.id for n in self.policy_ledger.nodes()}:
            self.policy_ledger.update(nid, status=status)
            return nid
        rep: Optional[PolicyReport] = rec.report
        self.policy_ledger.add(Node(id=nid, parent=parent_node, round=rec.iteration, kind="policy", status=status,
                                    score=None if rep is None else rep.value,
                                    cost=None if rep is None else rep.cpu_s, change=rec.change[:300],
                                    artifact_id=Artifact({"method.py": rec.code}).id,
                                    metrics={} if rep is None else {k: v for k, v in rep.diagnostics.items()
                                                                    if isinstance(v, (int, float))},
                                    meta={"label": rec.label}))
        return nid

    def _archive(self, rec: VersionRecord) -> None:
        d = f"history/r{rec.index:04d}_{rec.label or 'v'}"
        self._save(f"{d}/method.py", rec.code)
        if rec.report is not None:
            self._save(f"{d}/report.json", json.dumps(rec.report.summary(), indent=1, default=float))
            if rec.report.sweep:
                self._save(f"{d}/proposal_results/beta_sweep.json", json.dumps(rec.report.sweep, indent=1, default=float))
            self._save(f"{d}/proposal_results/policy_execution_traces.jsonl", rec.report.traces_jsonl())

    def _eval(self, code: str, label: str, idx: int, sweep: bool = False, worlds=None) -> PolicyReport:
        rep = self.replay.evaluate(code, worlds or self.worlds, manifests=self.manifests, sweep=sweep, label=label,
                                   policy_id=Artifact({"method.py": code}).id)
        self.meter.add_replay(rep.n_episodes, rep.cpu_s, rep.wall_s)
        return rep

    def _context_fn(self, q: OnlineQuestion, t: int):
        prob = self.task.describe()
        hist = history_records(self.worlds[-8:])
        base_score = self.seed_eval.score

        def fn(parent, parent_ws, b, a) -> AttemptContext:
            lineage = [record_of(n) for n in q.tree.nodes() if n.branch == b and n.attempt < a]
            siblings = [record_of(n) for n in q.tree.non_root() if n.branch != b]
            direction = dict(q.directions.get(b, {}))
            dtext = []
            if self.cfg.guidance and self.guidance is not None and self.guidance.text:
                dtext.append(self.guidance.text)
            if direction.get("direction"):
                dtext.append(f"Direction assigned to this branch: {direction['direction']}.")
            return AttemptContext(prob, parent.id, parent_ws, parent.score, b, a, t, lineage, siblings, hist,
                                  base_score, direction, "\n".join(dtext), self.task.editable())
        return fn

    def _calls_left(self) -> Optional[int]:
        if self.cfg.max_calls is None:
            return None
        return max(0, self.cfg.max_calls - self.meter.agent_calls)

    # ---------------------------------------------------------------------- run
    def run(self) -> ImprovementResult:
        c = self.cfg
        t_start = time.time()
        self.seed_eval = self.task.evaluate(program_only(self.seed_artifact), seed=c.seed)
        best_art, best_eval = program_only(self.seed_artifact), self.seed_eval
        best_score = best_eval.score if best_eval.fail_class == "ok" and best_eval.score is not None else float("-inf")
        policy = VersionRecord(self.rev_counter, self.initial_code, None, "initial policy pi_1", None, 0, "initial")
        self.rev_counter += 1
        policy_node = self._policy_node(policy, "deployed", None)
        self._archive(policy)
        self._save("history/baseline/method.py", self.baseline_code)
        stop = "rounds"
        for t in range(1, c.rounds + 1):
            left = self._calls_left()
            if left is not None and left <= 0:
                stop = "max_calls"
                break
            # ---- stage 1: online exploration with the deployed policy (stochastic, costly)
            ctx = GridPlanningContext(history=list(self.manifests), fallback_branch_count=c.branch_count,
                                      fallback_refine_count=c.refine_count, hard_max_branch_count=c.hard_max_branch,
                                      hard_max_refine_count=c.hard_max_refine, worker_cap=c.W, mode="live")
            root_art, root_eval = (best_art, best_eval) if c.root == "best" else \
                (program_only(self.seed_artifact), self.seed_eval)
            if c.guidance and self.worlds:
                self.guidance = self.summarizer.summarize(self.worlds, self.task.directions())
                self._save(f"trace_pool/iter{t:02d}/guidance.json", guidance_json(self.guidance))
            hint = self.guidance.as_provider_hint(c.guidance_strength) if (c.guidance and self.guidance) else None
            with self.runner.session(policy.code) as sess:
                req, perr = sess.plan_grid({}, ctx)
                plan, pnote = self.replay.validate_plan(req, perr)
                directions = self.provider.assign(plan.branch_count, t, hint)
                q = OnlineQuestion(task=self.task, agent=self.agent, root_artifact=root_art, root_eval=root_eval,
                                   W=c.W, plan=plan, K=c.K1, workers=c.agent_workers or c.W, store=self.store,
                                   meter=self.meter, directions=directions, seed=c.seed, round_index=t,
                                   world_id=f"iter{t:02d}", call_budget=left)
                q.context_fn = self._context_fn(q, t)
                t0 = time.time()
                out = sess.solve({}, q)
                self.meter.online_rounds += q.k
                self.meter.online_wall_s += time.time() - t0
            tree = q.tree
            tree.meta.update({"round": t, "policy": Artifact({"method.py": policy.code}).id,
                              "online_error": out.error, "violations": out.violations})
            self.worlds.append(tree)
            improved = [n for n in tree.non_root() if n.success and n.score is not None and n.score > best_score]
            if improved:
                bn = max(improved, key=lambda n: (n.score, -n.seq))
                best_score, best_art = float(bn.score), program_only(q.artifact(bn.id))
                best_eval = EvalOutcome(bn.score, True, bn.valid, "ok", None, bn.n_valid, bn.n_total,
                                        dict(bn.diagnostics))
            beta_live = default_beta_of(policy.code)
            man = live_manifest(t, tree, q, plan, req or plan, best_score, beta_live, tree.meta["policy"], q.calls)
            man["plan_error"] = perr or pnote
            self.manifests.append(man)
            if self.out:
                tree.save(self.out / f"trace_pool/iter{t:02d}/tree.json")
            self._save(f"trace_pool/iter{t:02d}/live_cycle_manifest.json", json.dumps(man, indent=1, default=float))
            tree.to_ledger(self.discovery_ledger, prefix=f"t{t}/")
            row = {"iteration": t, "calls": q.calls, "cum_calls": self.meter.agent_calls, "round_best": man["round_best"],
                   "best": best_score, "root": tree.root_score, "plan": plan.to_dict(), "N": q.N, "k": q.k,
                   "batch_sizes": list(q.batch_sizes), "policy": tree.meta["policy"][:10], "beta": beta_live,
                   "online_error": out.error, "violations": out.violations}
            # ---- stage 3: dreaming (cheap, offline)
            if c.dream and (t < c.rounds or c.dream_last) and (self._calls_left() is None or self._calls_left() > 0):
                policy, phase = self._dream(t, policy, policy_node)
                policy_node = f"r{policy.index:04d}"
                row.update(phase)
            row["wall_s"] = round(time.time() - t_start, 3)
            self.meter.close_iteration(t, best=best_score, calls=q.calls)
            self.trajectory.append(row)
        usage = llm_usage(*self.llms)
        usage["_cost"] = self.meter.snapshot()
        usage["_total"] = {"agent_calls": self.meter.agent_calls, "developer_calls": self.meter.developer_calls,
                           "usd": self.meter.agent_usd + self.meter.developer_usd}
        self._save("trajectory.json", json.dumps(self.trajectory, indent=1, default=float))
        self._save("final_policy/method.py", policy.code)
        res = ImprovementResult(method=self.method, baseline=self.seed_artifact, best=best_art,
                                ledger=self.discovery_ledger, trajectory=self.trajectory, usage=usage, stop_reason=stop,
                                out_dir=str(self.out) if self.out else None,
                                meta={"policy": policy.code, "policy_id": Artifact({"method.py": policy.code}).id,
                                      "policy_ledger": self.policy_ledger, "worlds": self.worlds,
                                      "manifests": self.manifests, "best_score": best_score,
                                      "seed_score": self.seed_eval.score, "cost": self.meter.snapshot(),
                                      "config": asdict(c)})
        if self.out:
            res.save(self.out)
        return res

    # ------------------------------------------------------------------ dreaming
    def _dream(self, t: int, incumbent: VersionRecord, incumbent_node: str) -> tuple[VersionRecord, dict]:
        c = self.cfg
        dev_idx = self.selector.dev_worlds(len(self.worlds))
        dev_worlds = [self.worlds[i] for i in dev_idx]
        inc = VersionRecord(incumbent.index, incumbent.code, None, incumbent.change, incumbent.parent, t,
                            incumbent.label)
        inc.report = self._eval(inc.code, "incumbent", inc.index)
        versions = [inc]
        forbidden = self._forbidden_terms()
        for m in range(c.n_revisions):
            fb_versions = [self._restricted(v, dev_idx) for v in versions]
            dctx = DevContext(t, fb_versions, [self._restricted(h, dev_idx) for h in self.history[-12:]],
                              [m_ for m_ in self.manifests], self.baseline_code, c.objective, c.W, forbidden,
                              first_in_phase=(m == 0))
            rev = self.developer.revise(dctx, seed=c.seed * 100003 + t * 101 + m)
            self.meter.add_developer(rev.usage)
            rec = VersionRecord(self.rev_counter, rev.code or "", None, rev.change, rev.parent, t, f"t{t}m{m + 1}")
            self.rev_counter += 1
            if rev.ok and static_check(rev.code).ok:
                rec.report = self._eval(rev.code, rec.label, rec.index)
            else:
                rec.change = f"rejected: {rev.error or static_check(rev.code or '').errors}"
            versions.append(rec)
            parent_node = f"r{rev.parent:04d}" if rev.parent is not None else incumbent_node
            if rec.report is not None or rec.code:
                self._policy_node(rec, "candidate" if rec.report is not None else "rejected", parent_node)
        reports = [v.report if v.report is not None else _failed_report(v) for v in versions]
        sel = self.selector.select(reports)
        chosen = versions[sel.index]
        if c.sweep and self.replay.sweep_grid:
            sweep_worlds = [self.worlds[i] for i in dev_idx] or self.worlds
            srep = self.replay.evaluate(chosen.code, sweep_worlds, manifests=self.manifests, sweep=True,
                                        label=chosen.label, policy_id=Artifact({"method.py": chosen.code}).id)
            self.meter.add_replay(srep.n_episodes, srep.cpu_s, srep.wall_s)
            chosen.report.sweep = srep.sweep
            chosen.report.sweep_episodes = srep.sweep_episodes
        for v in versions:
            if v is not chosen and v.index != incumbent.index:
                self._policy_node(v, "discard", None)
            self._archive(v)
        self._policy_node(chosen, "deployed", None)
        self.history.extend(v for v in versions if v.report is not None)
        phase = {"dream": {"t": t, "values": [round(r.value, 6) for r in reports], "selected": sel.index,
                           "selected_label": chosen.label, "reason": sel.reason, "delta_vs_incumbent":
                           reports[sel.index].value - reports[0].value, "details": _jsonable(sel.details),
                           "changes": [v.change[:160] for v in versions[1:]],
                           "replay_episodes": self.meter.replay_episodes, "dev_worlds": dev_idx,
                           "incumbent_diag": reports[0].diagnostics}}
        return chosen, phase

    def _restricted(self, v: VersionRecord, idx: list[int]) -> VersionRecord:
        """What the developer may see: reports restricted to the development worlds."""
        if v.report is None:
            return v
        r = v.report
        if len(r.per_world) != len(self.worlds):
            idx = self.selector.dev_worlds(len(r.per_world))      # the split in force when it was evaluated
        if len(idx) == len(r.per_world):
            return v
        eps = [r.episodes[i] for i in idx if i < len(r.episodes)]
        value = float(sum(r.per_world[i] for i in idx) / max(1, len(idx)))
        sweep = r.sweep
        if isinstance(self.replay.objective, ParetoSweepObjective):
            # the Pareto sweep covered every world: recompute it on the development worlds only, so
            # neither the value nor beta_sweep.json leaks held-out worlds to the developer
            keep = {e.world_id for e in eps}
            by_beta: dict = {}
            for e in r.sweep_episodes:
                if e.world_id in keep:
                    by_beta.setdefault(e.beta, []).append(e)
            sweep = self.replay.objective.sweep(by_beta) if by_beta else None
            value = sweep["reward"] if sweep else float("-inf")
        sub = PolicyReport(r.policy_id, r.label, r.objective, value, [r.per_world[i] for i in idx], eps, sweep, [],
                           diagnostics(eps), r.cpu_s, r.wall_s, len(eps))
        return VersionRecord(v.index, v.code, sub, v.change, v.parent, v.iteration, v.label)

    def _forbidden_terms(self) -> list[str]:
        """Trace-specific strings a policy must never contain (best cell ids and scores)."""
        terms = set()
        for w in self.worlds:
            b = w.best_node()
            if b.id != ROOT_ID:
                terms.add(b.id)
            if b.score is not None:
                terms.add(f"{b.score:.4f}")
        return sorted(terms)


def _failed_report(v: VersionRecord) -> PolicyReport:
    return PolicyReport(policy_id="", label=v.label, objective="rejected", value=float("-inf"), per_world=[],
                        episodes=[], diagnostics={"rejected": v.change})


def _jsonable(d: dict) -> dict:
    return json.loads(json.dumps(d, default=float))


# ------------------------------------------------------------------------------ run()
def as_task(domain, seed_artifact: Optional[Artifact] = None, llm_task=None, **kw) -> DiscoveryTask:
    """A :class:`DiscoveryTask` from a discovery task, a discovery domain (``as_task()``),
    or ANY :class:`rsi.core.Domain` (wrapped by :class:`DomainTask`)."""
    from .agent import DomainTask

    if isinstance(domain, DiscoveryTask) or (hasattr(domain, "as_task") and hasattr(domain, "evaluate_program")):
        if kw:
            raise TypeError(f"task_kwargs {sorted(kw)} only apply to an rsi.core Domain wrapped by DomainTask; "
                            "configure a DiscoveryTask / discovery domain directly")
        return domain if isinstance(domain, DiscoveryTask) else domain.as_task()
    seed = seed_artifact if seed_artifact is not None else domain.seed_artifact()
    return DomainTask(domain, seed, llm_task, **kw)


def run(domain, seed_artifact: Optional[Artifact] = None, *, llm_task=None, llm_propose=None, llm_develop=None,
        config: Optional[Config] = None, out_dir: Optional[str] = None, agent=None, developer=None,
        initial_policy=None, summarizer=None, task_kwargs: Optional[dict] = None) -> ImprovementResult:
    """Run Dream-RSI on any problem.

    Parameters
    ----------
    domain: an :class:`rsi.core.Domain` (programs scored by the locked grader on its
        ``evolve`` split), a discovery domain from :mod:`rsi.domains.discovery`, or a
        :class:`DiscoveryTask`.
    seed_artifact: the initial program / workspace (default ``domain.seed_artifact()``).
    llm_task: the frozen model the artifact itself calls (rsi.core Domains that need one).
    llm_propose: the discovery agent's LLM (wrapped in :class:`EditorAgent`); omitted ->
        ``domain.mock_agent()`` when the domain has an offline agent.
    llm_develop: the policy developer's LLM (:class:`LLMPolicyDeveloper`); omitted ->
        ``llm_propose`` if given, else the offline :class:`ParametricMutator`.
    config: :class:`Config`. ``config.dream=False`` runs Recursive Fixed Exploration.
    agent / developer / initial_policy / summarizer: explicit overrides.

    Returns an :class:`rsi.core.ImprovementResult`: ``best`` = best discovered program,
    ``trajectory`` = one row per live search (calls, best, plan, N, k, dreaming phase),
    ``meta["policy"]`` = the final deployed policy code, ``meta["policy_ledger"]``,
    ``meta["worlds"]`` (replay worlds), ``usage`` per LLM role plus the cost meter.
    """
    from .agent import EditorAgent
    from .developer import LLMPolicyDeveloper

    cfg = config or Config()
    task = as_task(domain, seed_artifact, llm_task, **(task_kwargs or {}))
    if agent is None:
        if llm_propose is not None:
            agent = EditorAgent(llm_propose, editable=task.editable())
        elif hasattr(domain, "mock_agent"):
            agent = domain.mock_agent()
        else:
            raise ValueError("no discovery agent: pass llm_propose=... or agent=...")
    dev_llm = llm_develop or llm_propose
    if developer is None:
        developer = LLMPolicyDeveloper(dev_llm, beta1=cfg.beta1, beta2=cfg.beta2, lam=cfg.lam,
                                       leakage_check=cfg.leakage_check) if dev_llm is not None else ParametricMutator()
    # meter every LLM role, including LLMs inside an explicitly passed agent / developer / summarizer
    llms = (llm_task, llm_propose, llm_develop, _llm_of(agent), _llm_of(developer), _llm_of(summarizer),
            _llm_of(getattr(task, "evaluator", None)))
    loop = DreamRSILoop(task, agent, config=cfg, developer=developer, initial_policy=initial_policy,
                        summarizer=summarizer, out_dir=out_dir, seed_artifact=seed_artifact, llms=llms)
    return loop.run()


def _llm_of(obj):
    """The :class:`rsi.core.LLM` behind an agent / developer / summarizer / evaluator, if any
    (``EditorAgent`` and ``LLMPolicyDeveloper`` wrap an Editor: ``RewriteEditor.llm`` or
    ``AgentEditor.cli``)."""
    from ..core.llm import LLM

    if obj is None or isinstance(obj, LLM):
        return obj
    for attr in ("llm", "editor"):
        x = getattr(obj, attr, None)
        if isinstance(x, LLM):
            return x
        if x is not None:
            for inner in ("llm", "cli"):
                y = getattr(x, inner, None)
                if isinstance(y, LLM):
                    return y
    return None
