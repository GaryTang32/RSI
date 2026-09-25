"""The autoresearch loop (upstream ``program.md`` "The experiment loop", run by
the framework rather than by the agent).

::

    setup:     fresh branch autoresearch/<tag>; results.tsv with the header only
    baseline:  run the unmodified files -> row with status keep
    LOOP until Budget (max experiments / wall / $ / STOP file) - never asks a human:
      1. context  <- program.md (re-read), in-scope files, results.tsv tail, kept-commit log
      2. proposal <- agent edits the editable files (one idea, one-line description)
      3. hardened: ScopeGuard rejects out-of-scope edits (never run)
      4. commit; run with the fixed budget (watchdog kill at kill_after)
      5. empty summary => crash; trivial crash => agent fix + re-run (<= max_fix_attempts)
      6. keep rule (strict by default; more runs if the rule needs them)
      7. append results.tsv row + Ledger node; keep => branch advances, else reset
    post hoc:  HiddenAudit of kept versions, Reeval with fresh seeds, Analyzer report

Everything is logged twice: the 5-column ``results.tsv`` (what the agent reads)
and a :class:`rsi.core.Ledger` tree (parent, diff, samples, verdict, program
version, crash kind, full source via :class:`~rsi.core.ArtifactStore`), which a
Dream-RSI style replay can consume.
"""
from __future__ import annotations

import json
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence

import numpy as np

from ..core.artifact import Artifact
from ..core.domain import Domain
from ..core.editors import Proposal, RewriteEditor
from ..core.ledger import ArtifactStore, Ledger, Node, new_id
from ..core.llm import LLM, ClaudeCLI
from ..core.run import Budget, ImprovementResult
from .agent import AgentContext, LLMResearchAgent, MockResearchAgent, ResearchAgent
from .guard import BudgetEnforcer, CrashPolicy, ScopeGuard
from .keep import KeepContext, KeepRule, NoiseCalibrator, Samples, make_keep_rule
from .program import ProgramSpec
from .results import ResultsLog, Workspace
from .task import DomainResearchTask, ResearchTask, RunOutcome


@dataclass
class Config:
    """Autoresearch run configuration. Defaults follow upstream (faithful keep
    rule, 100 experiments ~ one night) with the hardened isolation switched on."""

    max_experiments: Optional[int] = 100        # experiments after the baseline
    max_runs: Optional[int] = None               # training runs incl. baseline/repeats/fixes (equal-compute comparisons)
    max_wall_s: Optional[float] = None
    max_usd: Optional[float] = None
    stop_dir: Optional[str] = None               # a file named STOP here halts the loop (default: out_dir)
    mode: str = "hardened"                       # "faithful" | "hardened"
    keep_rule: Any = "strict"                    # "strict" | "rigor" | "simplicity" | "rrsi" | KeepRule | Gate
    keep_kwargs: dict = field(default_factory=dict)
    program: str = "upstream"                    # preset name or path to program.md (re-read every experiment)
    tag: str = "run"
    run_seed: int = 0                            # pinned framework seed for every run (upstream pins seed 42)
    max_fix_attempts: int = 3                    # "more than a few attempts" -> give up
    max_propose_attempts: int = 3
    max_consecutive_invalid: int = 5
    history_rows: int = 40                       # results.tsv rows shown to the agent
    noise_runs: int = 0                          # NoiseCalibrator re-runs of the baseline (5 suggested)
    val_resample_every: Optional[int] = None     # ablation: new validation epoch every M experiments
    hidden_audit: bool = True                    # post-hoc audit of every keep (never shown to the agent)
    audit_discards: bool = False
    reeval_seeds: int = 0                        # post-hoc fresh-seed re-runs of baseline and final best
    reeval_seed_base: int = 10_000
    reeval_mode: str = "hardened"                # honest re-eval uses the locked grader, whatever ``mode`` the loop ran in
    workers: int = 1                             # >1: parallel "don't wait" variant (see rsi.autoresearch.parallel)
    executor: str = "local"                      # "local" | "slurm" (fake SLURM) for workers > 1
    workspace: str = "memory"                    # "memory" | "git" (a real repository under out_dir/workspace)
    persist: bool = True
    overwrite: bool = False                      # reuse an out_dir that holds an earlier run (else refuse)
    plot: bool = True
    seed: int = 0                                # agent-side RNG seed (mock agent, LLM sample seeds)

    def to_json(self) -> dict:
        d = asdict(self)
        d["keep_rule"] = self.keep_rule if isinstance(self.keep_rule, str) else type(self.keep_rule).__name__
        return d


class AutoresearchLoop:
    """One greedy chain on one branch. See the module docstring for the steps."""

    method = "autoresearch"

    def __init__(self, task: ResearchTask, agent: ResearchAgent, config: Optional[Config] = None, *,
                 keep_rule: Optional[KeepRule] = None, program: Optional[ProgramSpec] = None,
                 out_dir: Optional[str | Path] = None, llms: Sequence[Optional[LLM]] = (),
                 seed_artifact: Optional[Artifact] = None, executor=None) -> None:
        self.task, self.agent = task, agent
        self.cfg = config or Config()
        if self.cfg.mode not in ("faithful", "hardened"):
            raise ValueError("mode must be 'faithful' or 'hardened'")
        self.keep = keep_rule or make_keep_rule(self.cfg.keep_rule, **self.cfg.keep_kwargs)
        self.out = Path(out_dir) if out_dir else Path(tempfile.mkdtemp(prefix="rsi_autoresearch_"))
        self.out.mkdir(parents=True, exist_ok=True)
        self._program_path = None if (program is not None or self.cfg.program in ("upstream", "xgboost", "simplify")) \
            else Path(self.cfg.program)
        self.program = program or ProgramSpec.load(self.cfg.program)
        self.programs = {self.program.version: self.program.label}
        p = self.cfg.persist
        if p and (self.out / "ledger.jsonl").exists():
            if not self.cfg.overwrite:
                raise FileExistsError(f"{self.out} already holds a run (ledger.jsonl); a run must be fresh "
                                      "(upstream: the branch must not exist). Use a new out_dir or Config(overwrite=True).")
            import shutil

            for name in ("ledger.jsonl", "results.tsv", "groundtruth_all.tsv", "summary.json", "trajectory.json"):
                (self.out / name).unlink(missing_ok=True)
            shutil.rmtree(self.out / "logs", ignore_errors=True)
            shutil.rmtree(self.out / "workspace", ignore_errors=True)
        self.ledger = Ledger(self.out / "ledger.jsonl" if p else None)
        self.store = ArtifactStore(self.out / "artifacts")
        self.results = ResultsLog(self.out / "results.tsv" if p else None, metric_name=task.metric)
        self.ws = Workspace(self.store, backend=self.cfg.workspace,
                            root=self.out / "workspace" if self.cfg.workspace == "git" else None)
        self.guard = ScopeGuard(task.editable_paths, task.locked_paths, sealed=task.sealed_files(),
                                tamper=getattr(task, "tamper_patterns", ()))
        self.enforcer = BudgetEnforcer(task.budget)
        self.policy = CrashPolicy(self.cfg.max_fix_attempts)
        self.budget = Budget(max_rounds=self.cfg.max_experiments, max_rollouts=self.cfg.max_runs,
                             max_wall_s=self.cfg.max_wall_s, max_usd=self.cfg.max_usd,
                             stop_dir=self.cfg.stop_dir or str(self.out))
        self.llms = [l for l in llms if l is not None]
        self.executor = executor
        self.seed_art = seed_artifact
        self.inc: dict = {}
        self.keeps: list[tuple[str, Samples]] = []
        self.seen: dict[str, str] = {}
        self.trajectory: list[dict] = []
        self.n_rounds = 0
        self.n_runs = 0
        self.n_experiments = 0
        self.n_invalid_streak = 0
        self.val_epoch = 0
        self.noise = None
        self.stop_reason = ""
        self.t0 = time.time()
        self.counters = {"rejected": 0, "duplicate": 0, "invalid": 0, "fix_attempts": 0, "fixed": 0}

    # ------------------------------------------------------------------ helpers
    def _log_path(self, name: str) -> Optional[str]:
        return str(self.out / "logs" / f"{name}.log") if self.cfg.persist else None

    def _run(self, artifact: Artifact, seed: int, name: str) -> RunOutcome:
        self.n_runs += 1
        if self.executor is not None:
            return self.executor.run(self.task, artifact, seed=seed, mode=self.cfg.mode,
                                     log_path=self._log_path(name), val_epoch=self.val_epoch)
        return self.task.run(artifact, seed=seed, mode=self.cfg.mode, log_path=self._log_path(name),
                             val_epoch=self.val_epoch)

    def _seeds(self, n: int) -> list[int]:
        base = self.cfg.run_seed
        return [base + i for i in range(n)] if self.keep.vary_seed else [base] * n

    def usd(self) -> float:
        return float(sum(l.meter.total().cost_usd for l in self.llms))

    def _editable_loc(self, art: Artifact) -> int:
        from .guard import matches

        return sum(len(v.splitlines()) for k, v in art.files.items() if matches(k, self.task.editable_paths))

    def _diff_counts(self, a: Artifact, b: Artifact) -> tuple[int, int]:
        add = rem = 0
        for line in a.diff(b, context=0).splitlines():
            if line.startswith("+") and not line.startswith("+++"):
                add += 1
            elif line.startswith("-") and not line.startswith("---"):
                rem += 1
        return add, rem

    def best_value(self) -> Optional[float]:
        vals = [s.mean for _, s in self.keeps]
        if not vals:
            return None
        return min(vals) if self.task.direction == "min" else max(vals)

    def _maybe_reload_program(self) -> None:
        if self._program_path is None or not self._program_path.exists():
            return
        text = self._program_path.read_text()
        if text != self.program.text:
            self.program = self.program.edit(text, author="human")
            self.programs[self.program.version] = self.program.label

    def context(self) -> AgentContext:
        self._maybe_reload_program()
        return AgentContext(
            program=self.program.render(self.task, self.cfg.mode), artifact=self.inc["artifact"],
            editable=self.task.editable_paths, locked=self.task.locked_paths,
            results_tsv=self.results.text(last=self.cfg.history_rows), git_log=self.ws.log_text(20),
            task_brief=self.task.describe(), metric=self.task.metric, direction=self.task.direction,
            best=self.best_value(), experiment=self.n_rounds, seed=self.cfg.seed)

    # ------------------------------------------------------------------ recording
    def _record(self, *, art: Artifact, desc: str, status: str, samples: Optional[Samples], outcome: Optional[RunOutcome],
                commit: str, ledger_status: Optional[str] = None, kind: str = "candidate", verdict=None,
                meta: Optional[dict] = None, tsv: bool = True) -> Node:
        metric = samples.mean if samples is not None and status != "crash" else None
        mem = samples.memory_gb if samples is not None else 0.0
        if tsv:
            self.results.append(commit, metric, mem, status, desc)
        parent = self.inc.get("node")
        base_art = self.inc.get("artifact")
        m = {"memory_gb": mem, "wall_s": round(outcome.wall_s, 3) if outcome else 0.0,
             "samples": list(samples.values) if samples else [], "val_epoch": self.val_epoch}
        truth = self.task.truth(art)
        if truth is not None:
            m["truth"] = truth
        node = Node(id=new_id("exp"), parent=parent, round=self.n_rounds, kind=kind,
                    status=ledger_status or status, score=metric, cost=mem, change=desc, artifact_id=art.id,
                    diff=(base_art.diff(art)[:20000] if base_art is not None else ""), metrics=m,
                    meta={"commit": commit, "program_version": self.program.version, "keep_rule": self.keep.name,
                          "mode": self.cfg.mode, "verdict": (verdict.reason if verdict is not None else None),
                          "verdict_details": (verdict.details if verdict is not None else None),
                          "crash_reason": outcome.crash_reason if outcome else None,
                          "summary": outcome.summary if outcome else None, **(meta or {})})
        self.store.put(art)
        self.ledger.add(node)
        best = self.best_value()
        self.trajectory.append({"exp": self.n_rounds, "status": node.status, "metric": metric, "best": best,
                                "description": desc, "commit": commit, "wall_s": m["wall_s"],
                                "t": round(time.time() - self.t0, 3), "kind": (meta or {}).get("edit_kind"),
                                "truth": truth, "samples": m["samples"]})
        return node

    # ------------------------------------------------------------------ phases
    def setup(self) -> None:
        self.task.prepare()
        self.results.init()
        base = self.seed_art or self.task.seed_artifact()
        sha = self.ws.init_run(self.cfg.tag, base, "baseline")
        self.inc = {"sha": sha, "artifact": base, "node": None}
        if self.cfg.persist:
            if self._program_path is None:
                self.program.save(self.out / "program.md")
            (self.out / "config.json").write_text(json.dumps(
                {"config": self.cfg.to_json(), "task": self.task.name, "metric": self.task.metric,
                 "direction": self.task.direction, "budget": self.task.budget.to_json(),
                 "keep_rule": self.keep.to_json(), "agent": getattr(self.agent, "name", "agent"),
                 "program_version": self.program.version}, indent=1, default=str))

    def baseline(self) -> Node:
        """"The first run": run the files as they are -> status keep."""
        art = self.inc["artifact"]
        vals, outs = [], []
        for i, s in enumerate(self._seeds(self.keep.repeats)):
            o = self._run(art, s, f"baseline_{i}")
            outs.append(o)
            if o.crashed:
                raise RuntimeError(f"baseline run crashed: {o.crash_reason}\n{o.tail(30)}")
            vals.append(o.metric)
        samples = Samples(vals, memory_gb=outs[-1].memory_gb, loc=self._editable_loc(art), artifact_id=art.id)
        node = self._record(art=art, desc="baseline", status="keep", samples=samples, outcome=outs[-1],
                            commit=self.inc["sha"][:7], kind="baseline")
        self.inc.update(node=node.id, samples=samples)
        self.keeps.append((node.id, samples))
        self.seen[art.id] = node.id
        self.trajectory[-1]["best"] = self.best_value()
        if self.cfg.noise_runs:
            extra = [self._run(art, self.cfg.run_seed + 100 + i, f"noise_{i}").metric for i in range(self.cfg.noise_runs)]
            extra = [v for v in extra if v is not None] + vals[:1]
            if len(extra) >= 2:
                self.noise = NoiseCalibrator(len(extra)).estimate(extra)
                self.ledger.update(node.id, meta={"noise": self.noise.to_json(), "noise_runs": extra})
        return node

    def rewind(self, commit: str) -> None:
        """Move the branch tip back to an earlier *kept* commit ("allowed, but very very
        sparingly"). Counted in ``meta["workspace"]["rewinds"]``."""
        node = next((n for n in reversed(self.ledger.nodes()) if n.status == "keep"
                     and str(n.meta.get("commit", "")).startswith(commit[:7])), None)
        if node is None:
            raise KeyError(f"no kept commit {commit!r}")
        sha = next(c.sha for c in self.ws.commits.values() if c.sha.startswith(node.meta["commit"]))
        self.ws.rewind(sha)
        samples = next(s for nid, s in self.keeps if nid == node.id)
        self.inc = {"sha": sha, "artifact": self.ws.artifact(sha), "node": node.id, "samples": samples}
        self.ledger.update(node.id, meta={"rewound_to_at_round": self.n_rounds})

    def _propose(self, ctx: AgentContext) -> Proposal:
        prop = Proposal(None, error="not attempted")
        for a in range(self.cfg.max_propose_attempts):
            ctx.seed = self.cfg.seed + 7 * a
            prop = self.agent.propose(ctx)
            if prop.ok and prop.artifact != ctx.artifact:
                return prop
        return prop

    def _resample_val(self) -> None:
        """Ablation: switch to a new validation epoch and re-score the incumbent on it."""
        self.val_epoch += 1
        art = self.inc["artifact"]
        vals = [self._run(art, s, f"rescore_{self.val_epoch}_{i}").metric for i, s in
                enumerate(self._seeds(self.keep.repeats))]
        vals = [v for v in vals if v is not None]
        if vals:
            samples = Samples(vals, memory_gb=self.inc["samples"].memory_gb, loc=self.inc["samples"].loc,
                              artifact_id=art.id)
            self.inc["samples"] = samples
            self.keeps = [(self.inc["node"], samples)]
            self.ledger.update(self.inc["node"], meta={f"rescore_epoch_{self.val_epoch}": vals})

    def _next_candidate(self):
        """Propose, scope-check and de-duplicate one candidate. Returns
        ``(ctx, cand, desc, emeta)`` or None when the turn produced nothing to run."""
        self.n_rounds += 1
        if self.cfg.val_resample_every and self.n_rounds > 1 and (self.n_rounds - 1) % self.cfg.val_resample_every == 0:
            self._resample_val()
        ctx = self.context()
        prop = self._propose(ctx)
        inc_art: Artifact = self.inc["artifact"]
        if not prop.ok or prop.artifact == inc_art:
            self.counters["invalid"] += 1
            self.n_invalid_streak += 1
            self.ledger.add(Node(id=new_id("exp"), parent=self.inc["node"], round=self.n_rounds, kind="candidate",
                                 status="invalid", change=prop.change or "", meta={"error": prop.error}))
            return None
        self.n_invalid_streak = 0
        cand, desc = prop.artifact, (prop.change or "(no description)")
        emeta = {"edit_kind": prop.meta.get("kind"), "edit": prop.meta.get("edit"), "hypothesis": prop.hypothesis,
                 "components": prop.components}
        if self.cfg.mode == "hardened":
            viol = self.guard.check(inc_art, cand)
            if viol:
                self.counters["rejected"] += 1
                self.n_experiments += 1
                self._record(art=cand, desc=f"REJECTED ({', '.join(map(str, viol))}) | {desc}", status="discard",
                             samples=None, outcome=None, commit=cand.id[:7], ledger_status="rejected",
                             meta={**emeta, "violations": [str(v) for v in viol]})
                return None
        if self.keep.never_repeat and cand.id in self.seen:
            self.counters["duplicate"] += 1
            self.n_invalid_streak += 1
            self.ledger.add(Node(id=new_id("exp"), parent=self.inc["node"], round=self.n_rounds, status="duplicate",
                                 change=desc, artifact_id=cand.id, meta={**emeta, "same_as": self.seen[cand.id]}))
            return None
        self.seen[cand.id] = "pending"
        return ctx, cand, desc, emeta

    def step(self) -> Optional[Node]:
        """One experiment. Returns the ledger node (None if the turn produced nothing)."""
        nxt = self._next_candidate()
        if nxt is None:
            return None
        ctx, cand, desc, emeta = nxt
        self.n_experiments += 1
        sha = self.ws.commit(cand, desc)
        name = f"exp{self.n_rounds:04d}"
        outcome = self._run(cand, self._seeds(self.keep.repeats)[0], name)
        return self._settle(ctx, cand, desc, sha, outcome, emeta, name, committed=True)

    def _settle(self, ctx: AgentContext, cand: Artifact, desc: str, sha: str, outcome: RunOutcome, emeta: dict,
                name: str, committed: bool = True) -> Node:
        """Crash handling, extra runs for the keep rule, the keep decision, logging.
        ``committed``: the candidate is HEAD (sequential loop) and must be reset on
        discard; otherwise (parallel loop) it is committed only when kept."""
        seeds = self._seeds(self.keep.repeats)
        kind = self.policy.kind(outcome)
        attempts = 0
        while outcome.crashed and self.policy.should_fix(kind, attempts):
            attempts += 1
            self.counters["fix_attempts"] += 1
            fixed = self.agent.fix_crash(ctx, cand, desc, outcome.tail(50))
            if fixed is None or not fixed.ok:
                break
            if self.cfg.mode == "hardened" and self.guard.check(ctx.artifact, fixed.artifact):
                break
            cand = fixed.artifact
            if committed:
                sha = self.ws.amend(cand, desc)
            outcome = self._run(cand, seeds[0], f"{name}_fix{attempts}")
            kind = self.policy.kind(outcome)
            if not outcome.crashed:
                self.counters["fixed"] += 1
        emeta.update({"fix_attempts": attempts, "crash_kind": kind if outcome.crashed else None,
                      "budget_event": self.enforcer.classify(outcome)})
        if outcome.crashed:
            self.policy.record(kind)
            if committed:
                self.ws.reset_to(self.inc["sha"])
            node = self._record(art=cand, desc=desc, status="crash", samples=None, outcome=outcome,
                                commit=sha[:7], meta=emeta)
            self.seen[cand.id] = node.id
            return node
        vals, outs = [outcome.metric], [outcome]
        ref = self.keep.reference(self.inc["samples"], [s for _, s in self.keeps], self.task.direction)
        status_override = None
        if self.keep.repeats > 1 and not self.keep.early_reject(vals[0], ref, self.task.direction):
            for i, s in enumerate(seeds[1:], start=1):
                o = self._run(cand, s, f"{name}_r{i}")
                outs.append(o)
                if o.crashed:
                    status_override = "crash"
                    emeta["crash_kind"] = self.policy.kind(o)
                    break
                vals.append(o.metric)
        samples = Samples(vals, memory_gb=float(np.max([o.memory_gb for o in outs])), loc=self._editable_loc(cand),
                          artifact_id=cand.id)
        add, rem = self._diff_counts(self.inc["artifact"], cand)
        kctx = KeepContext(direction=self.task.direction, best=self.best_value(),
                           delta=self.noise.delta if self.noise else 0.0, experiment=self.n_rounds,
                           lines_added=add, lines_removed=rem)
        verdict = None
        if status_override:
            status = status_override
        elif self.keep.repeats > 1 and len(vals) < self.keep.repeats:
            status = "discard"
            emeta["early_reject"] = True
        else:
            verdict = self.keep.decide(samples, ref, kctx)
            status = "keep" if verdict.accept else "discard"
        if status == "keep" and outcome.over_budget:
            status = "discard"
            desc = f"{desc} (over budget: {outcome.wall_s:.0f}s)"
        if status == "keep":
            if not committed:
                sha = self.ws.commit(cand, desc)
            node = self._record(art=cand, desc=desc, status="keep", samples=samples, outcome=outcome, commit=sha[:7],
                                verdict=verdict, meta=emeta)
            self.inc = {"sha": sha, "artifact": cand, "node": node.id, "samples": samples}
            self.keeps.append((node.id, samples))
        else:
            if committed:
                self.ws.reset_to(self.inc["sha"])
            node = self._record(art=cand, desc=desc, status=status, samples=samples if status != "crash" else None,
                                outcome=outcome, commit=sha[:7], verdict=verdict, meta=emeta)
        self.seen[cand.id] = node.id
        return node

    # ------------------------------------------------------------------ post hoc
    def hidden_audit(self) -> list[dict]:
        from .analysis import HiddenAudit

        statuses = ("keep", "discard") if self.cfg.audit_discards else ("keep",)
        rows = HiddenAudit(self.task).audit_ledger(self.ledger, self.store, statuses=statuses,
                                                   seed=self.cfg.run_seed)
        if self.cfg.persist and rows:
            HiddenAudit.write_tsv(rows, self.out / "groundtruth_all.tsv", self.task.metric)
        return rows

    def reeval(self) -> dict:
        from .analysis import Reeval

        seeds = [self.cfg.reeval_seed_base + i for i in range(self.cfg.reeval_seeds)]
        base_art = self.ws.artifact(self.ws.log(10_000)[-1].sha)
        rv = Reeval(self.task, mode=self.cfg.reeval_mode or self.cfg.mode)
        out = {"final": rv.run(self.inc["artifact"], seeds), "baseline": rv.run(base_art, seeds), "mode": rv.mode}
        rec = self.inc["samples"].mean
        honest = out["final"]["mean"]
        if honest == honest:  # not NaN
            out["recorded_best"] = rec
            out["optimism_gap"] = (honest - rec) if self.task.direction == "min" else (rec - honest)
        return out

    def run(self) -> ImprovementResult:
        self.setup()
        self.baseline()
        while True:
            why = self.budget.exhausted(rounds=self.n_rounds, rollouts=self.n_runs, usd=self.usd())
            if why:
                self.stop_reason = why
                break
            if self.n_invalid_streak >= self.cfg.max_consecutive_invalid:
                self.stop_reason = "agent_failed"
                break
            self.step()
        return self.finish()

    def finish(self) -> ImprovementResult:
        from .analysis import Analyzer

        wall = time.time() - self.t0
        meta: dict = {"results_tsv": str(self.out / "results.tsv") if self.cfg.persist else None,
                      "counters": dict(self.counters), "crash_kinds": dict(self.policy.counts),
                      "scope": {"checked": self.guard.n_checked, "rejected": self.guard.n_rejected},
                      "budget_events": {"killed": self.enforcer.n_killed, "over_budget": self.enforcer.n_over},
                      "programs": dict(self.programs), "n_experiments": self.n_experiments, "n_runs": self.n_runs,
                      "wall_s": wall, "noise": self.noise.to_json() if self.noise else None,
                      "workspace": {"branch": self.ws.branch, "head": self.ws.head()[:7], "resets": self.ws.n_resets,
                                    "rewinds": self.ws.n_rewinds},
                      "final_samples": list(self.inc["samples"].values), "val_epochs": self.val_epoch}
        an = Analyzer.from_ledger(self.ledger, self.task.direction, metric=self.task.metric)
        meta["analysis"] = an.summary(wall_s=wall)
        if self.cfg.plot and self.cfg.persist:
            try:
                an.plot(self.out / "progress.png", title=f"autoresearch: {self.task.name}")
            except Exception as e:  # noqa: BLE001 - plotting must never fail a run
                meta["plot_error"] = repr(e)
        if self.cfg.hidden_audit:
            meta["audit"] = self.hidden_audit()
        if self.cfg.reeval_seeds:
            meta["reeval"] = self.reeval()
        usage = {}
        for l in self.llms:
            for k, v in l.meter.snapshot().items():
                usage[f"{l.name}:{k}" if k != "_total" else f"{l.name}:_total"] = v
        tot = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
        for l in self.llms:
            t = l.meter.total()
            tot["calls"] += t.calls
            tot["input_tokens"] += t.input_tokens
            tot["output_tokens"] += t.output_tokens
            tot["cost_usd"] += t.cost_usd
        usage["_total"] = tot
        res = ImprovementResult(method=self.method, baseline=self.ws.artifact(self.ws.log(10_000)[-1].sha),
                                best=self.inc["artifact"], ledger=self.ledger, trajectory=self.trajectory,
                                usage=usage, stop_reason=self.stop_reason, out_dir=str(self.out), meta=meta)
        if self.cfg.persist:
            (self.out / "summary.json").write_text(json.dumps({**res.summary(), "meta": meta}, indent=1, default=str))
            (self.out / "trajectory.json").write_text(json.dumps(self.trajectory, indent=1, default=float))
            self.inc["artifact"].to_dir(self.out / "best_artifact", clean=True)
        return res


# --------------------------------------------------------------------------- entry point
def make_agent(task: ResearchTask, llm_propose: Optional[LLM], *, editor: str = "rewrite", seed: int = 0,
               **mock_kw) -> ResearchAgent:
    """LLM agent when ``llm_propose`` is given (RewriteEditor, or AgentEditor for a
    ClaudeCLI with ``editor="agent"``), else the scripted MockResearchAgent."""
    if llm_propose is None:
        pool = task.mock_edit_pool()
        if not pool:
            raise ValueError(f"task {task.name} has no mock edit pool; pass llm_propose= or agent=")
        return MockResearchAgent(pool, seed=seed, **mock_kw)
    if editor == "agent":
        from ..core.editors import AgentEditor

        inner = getattr(llm_propose, "inner", llm_propose)
        if not isinstance(inner, ClaudeCLI):
            raise ValueError("editor='agent' needs a ClaudeCLI backend")
        return LLMResearchAgent(AgentEditor(inner))
    return LLMResearchAgent(RewriteEditor(llm_propose))


def run(domain_or_task, seed_artifact: Optional[Artifact] = None, *, llm_task: Optional[LLM] = None,
        llm_propose: Optional[LLM] = None, config: Optional[Config] = None, out_dir: Optional[str | Path] = None,
        agent: Optional[ResearchAgent] = None, keep_rule: Optional[KeepRule] = None,
        program: Optional[ProgramSpec] = None, editor: str = "rewrite", mock: Optional[dict] = None,
        task_kwargs: Optional[dict] = None) -> ImprovementResult:
    """Run autoresearch on a :class:`ResearchTask` or on any :class:`rsi.core.Domain`.

    Parameters
    ----------
    domain_or_task:
        a ResearchTask (tinylm, tabular, landscape, your own ScriptResearchTask),
        or a Domain, which is wrapped in :class:`DomainResearchTask` (one experiment
        = one fixed-size evaluation on ``evolve``; audits on ``holdout``/``ood``).
    seed_artifact:
        the starting files (default: the task's seed artifact).
    llm_task:
        the frozen model a Domain's artifact uses (ignored for script tasks).
    llm_propose:
        the research agent's model; None -> :class:`MockResearchAgent` on the task's
        scripted edit pool (``mock`` = its keyword arguments).
    config:
        :class:`Config` (mode, keep rule, budget, audits ...).

    Returns an :class:`rsi.core.ImprovementResult` whose ``meta`` holds the
    results.tsv path, the Analyzer summary, the hidden-audit table and re-evals.
    """
    cfg = config or Config()
    if isinstance(domain_or_task, Domain):
        task = DomainResearchTask(domain_or_task, llm_task, seed_artifact=seed_artifact, **(task_kwargs or {}))
    else:
        task = domain_or_task
    ag = agent or make_agent(task, llm_propose, editor=editor, seed=cfg.seed, **(mock or {}))
    llms = [llm_propose, llm_task if isinstance(task, DomainResearchTask) else None]
    if cfg.workers > 1:
        from .parallel import ParallelAutoresearchLoop

        loop = ParallelAutoresearchLoop(task, ag, cfg, keep_rule=keep_rule, program=program, out_dir=out_dir,
                                        llms=llms, seed_artifact=seed_artifact)
    else:
        loop = AutoresearchLoop(task, ag, cfg, keep_rule=keep_rule, program=program, out_dir=out_dir, llms=llms,
                                seed_artifact=seed_artifact)
    return loop.run()
