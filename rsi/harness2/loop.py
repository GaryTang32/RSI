"""Task-level recursion: Propose-Probe-Compose on ONE open task, in parallel (width) or sequential (depth) mode.

Budget identity per task [spec §3; code recursion.py, ledger.py]: R = 4k rollouts (k references, 2k candidates,
k composed re-executions) and P = 3k editor/composer sessions; invalid-proposal retries are attempts, not sessions;
the selector and the reflector are outside the ledger. :class:`TaskBudget` refuses anything past the identity, so a
task can be re-run only within it. Nothing here grades: a :class:`~.records.Rollout` never carries a score.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Mapping, Optional, Sequence

from ..core.artifact import Artifact
from ..core.domain import Domain
from ..core.llm import LLM
from ..core.tasks import Task
from ..trace import RunTracer
from . import context as C
from . import prompts as P
from .config import Config
from .records import Rollout, Stage, TaskRecord
from .roles import Session, reflect, run_stage, select
from .tree import diff_summary, diversity

#: on-disk mode tokens of the reference code (part of the selector's anonymisation seed)
MODE_TOKEN = {"parallel": "agg", "sequential": "seq"}


class BudgetExceeded(RuntimeError):
    """A rollout or session beyond the per-task identity R = 4k, P = 3k."""


@dataclass
class TaskBudget:
    k: int
    rollouts: int = 0
    sessions: int = 0

    @property
    def max_rollouts(self) -> int:
        return 4 * self.k

    @property
    def max_sessions(self) -> int:
        return 3 * self.k

    def charge_rollout(self, label: str) -> None:
        if self.rollouts >= self.max_rollouts:
            raise BudgetExceeded(f"rollout {label!r} would exceed R = 4k = {self.max_rollouts} for this task")
        self.rollouts += 1

    def charge_session(self, label: str) -> None:
        if self.sessions >= self.max_sessions:
            raise BudgetExceeded(f"session {label!r} would exceed P = 3k = {self.max_sessions} for this task")
        self.sessions += 1

    def assert_identity(self) -> None:
        if (self.rollouts, self.sessions) != (self.max_rollouts, self.max_sessions):
            raise BudgetExceeded(f"budget identity broken: {self.rollouts} rollouts / {self.sessions} sessions, "
                                 f"expected {self.max_rollouts} / {self.max_sessions}")


def _seed(*parts) -> int:
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:8], 16) % 1_000_000


def task_text(domain: Domain, task: Task) -> str:
    """The task as the agent receives it (``domain.task_prompt(task)`` if the domain has one)."""
    hook = getattr(domain, "task_prompt", None)
    if callable(hook):
        return str(hook(task))
    return task.input if isinstance(task.input, str) else repr(task.input)


def task_source(domain: Domain, task: Task) -> dict[str, str]:
    """``context/source/`` files (``domain.task_source(task)`` if the domain has one, else none)."""
    hook = getattr(domain, "task_source", None)
    return {str(k): str(v) for k, v in (hook(task) or {}).items()} if callable(hook) else {}


@dataclass
class TaskRunner:
    """One open task: the only door to the executor (rollouts) and to the editor/composer sessions."""

    domain: Domain
    task: Task                       # the OPEN task: no target, no answer-like meta keys
    domain_key: str
    base_tree: Artifact              # H0, the common execution harness every task starts from
    llm_task: LLM
    session: Session                 # editors and composers
    select_session: Session          # selector and reflector (the reference's selector channel)
    cfg: Config
    library: Mapping[str, str] = field(default_factory=dict)   # this domain's library, earlier tasks only
    slots: Mapping[str, str] = field(default_factory=dict)
    editable: Sequence[str] = ()
    interface: str = ""
    tracer: Optional[RunTracer] = None
    round: int = 0
    stream: Optional[object] = None  # TaskStream: every rollout must be of its current task

    def __post_init__(self) -> None:
        self.k = self.cfg.k
        self.budget = TaskBudget(self.k)
        self.rec = TaskRecord(task_id=self.task.id, domain_key=self.domain_key, mode=self.cfg.mode, k=self.k,
                              base_tree=self.base_tree)
        self.text = task_text(self.domain, self.task)
        self.source = task_source(self.domain, self.task)
        self.decisions: dict[str, str] = {}

    # ------------------------------------------------------------------ doors
    def roll(self, label: str, tree: Artifact) -> Rollout:
        """Execute ``tree`` once on the current task (same model, fresh seed per label); never graded."""
        if self.stream is not None:
            self.stream.assert_current(self.task.id)
        self.budget.charge_rollout(label)
        seed = _seed(self.cfg.seed, self.task.id, label)
        ex = self.domain.rollout(tree, self.task, seed=seed, llm=self.llm_task)
        r = Rollout(label=label, tree_id=tree.id, seed=seed, execution=ex)
        self.rec.rollouts[label] = r
        self.rec.trees[label] = tree
        if self.tracer is not None and self.tracer.enabled:
            self.tracer.event("rollout", self.round, task=self.task.id, label=label, tree=tree.short_id, seed=seed,
                              ok=r.ok, delivered=r.delivered, steps=ex.steps, tokens=ex.tokens, error=ex.error,
                              output=str(ex.output or "")[:600])
        return r

    def stage(self, label: str, role: str, prompt: str, ctx: Mapping[str, str], start: Artifact,
              fallback: Artifact, pool: Mapping[str, Artifact] = {}) -> Stage:
        self.budget.charge_session(label)
        st = run_stage(self.session, label=label, role=role, prompt=prompt, ctx=ctx, start=start,
                       fallback=fallback, pool=pool)
        self.rec.stages.append(st)
        self.decisions[label] = (st.notes or "(no notes)") if not st.fell_back else \
            f"(stage failed after {st.attempts} attempt(s) and inherited its starting harness) {st.error}"
        if self.tracer is not None and self.tracer.enabled:
            ch = diff_summary(start, st.tree)
            self.tracer.proposal(self.round, f"{self.task.id}/{label}", parent=start.short_id, prompt=st.prompt,
                                 reply=st.reply, change=(st.notes or "")[:1500], diff=start.diff(st.tree),
                                 components=ch["added"] + ch["modified"] + ch["removed"], error=st.error, role=role,
                                 tree=st.tree.short_id, kept_base=st.kept_base, fell_back=st.fell_back,
                                 attempts=st.attempts, adopted_from=st.adopted_from)
        return st

    def evidence(self, harness: Artifact, rollouts: Sequence[Rollout], pool=(), decisions: Mapping[str, str] = {}):
        lib = self.library if self.cfg.library else {}
        return C.build(self.text, harness=harness, rollouts=rollouts, pool=pool, decisions=decisions, library=lib,
                       source=self.source, obs_trunc=self.cfg.obs_trunc)

    def _prompt_kw(self) -> dict:
        return {"slots": self.slots, "editable": self.editable, "interface": self.interface}

    # ------------------------------------------------------------------ Propose / Probe / Compose
    def bases(self) -> Optional[list[Rollout]]:
        """k reference executions of the unmodified harness; None when none of them ran (``base-failed``)."""
        b = [self.roll(f"base{i}", self.base_tree) for i in range(1, self.k + 1)]
        return b if any(r.ok for r in b) else None

    def run_parallel(self, B: list[Rollout]) -> list[str]:
        """2k i.i.d. scoped candidates from the same evidence, one probe each, k independent composers."""
        base, k = self.base_tree, self.k
        cands = [self.stage(f"{d}{i}", d, P.propose(d, k, i, **self._prompt_kw()), self.evidence(base, B), base, base)
                 for i in range(1, k + 1) for d in ("inv", "dom")]
        pool = [(st.label, st.tree, self.roll(st.label, st.tree)) for st in cands]
        trees = {lab: t for lab, t, _ in pool}
        decisions = {st.label: self.decisions[st.label] for st in cands}
        finals = [self.stage(f"agg{j}", "composer", P.aggregate(k, j, **self._prompt_kw()),
                             self.evidence(base, B, pool, decisions), base, base, trees) for j in range(1, k + 1)]
        for st in finals:
            self.roll(st.label, st.tree)
        self.rec.notes.append(f"final diversity (pairwise line-Jaccard distance): "
                              f"{diversity([st.tree for st in finals]):.3f}")
        return [st.label for st in finals]

    def run_sequential(self, B: list[Rollout]) -> list[str]:
        """k chained rounds of {INV, DOM, compose}; each round edits the previous checkpoint."""
        k, cur = self.k, self.base_tree
        history: list[tuple[str, Artifact, Optional[Rollout]]] = []
        finals = []
        for t in range(1, k + 1):
            kw = self._prompt_kw()
            cands = [self.stage(f"{d}{t}", d, P.propose(d, k, t, round_t=t if k > 1 else None, **kw),
                                self.evidence(cur, B, history, dict(self.decisions)), cur, cur) for d in ("inv", "dom")]
            round_pool = history + [(st.label, st.tree, self.roll(st.label, st.tree)) for st in cands]
            job = P.aggregate(1, 1, **kw) if k == 1 else P.compose_round(t, k, **kw)
            c = self.stage(f"c{t}", "composer", job, self.evidence(cur, B, round_pool, dict(self.decisions)), cur, cur,
                           {lab: tr for lab, tr, _ in round_pool})
            history = round_pool + [(c.label, c.tree, self.roll(c.label, c.tree))]
            cur = c.tree
            finals.append(c.label)
        return finals

    def solve(self) -> TaskRecord:
        """Propose-Probe-Compose, then final selection; the deliverable is a composed harness's re-execution."""
        rec = self.rec
        B = self.bases()
        if B is None:
            rec.status = "base-failed"
            rec.notes.append("no reference rollout ran; the task stops without a deliverable")
            return rec
        finals = self.run_parallel(B) if self.cfg.mode == "parallel" else self.run_sequential(B)
        self.budget.assert_identity()
        sel, st = select(self.select_session, rec, finals, task_text=self.text, source=self.source,
                         attempts=self.cfg.selector_attempts, slots=self.slots, obs_trunc=self.cfg.obs_trunc,
                         mode_token=MODE_TOKEN[self.cfg.mode])
        if st is not None:
            rec.stages.append(st)
        rec.selection, rec.adopted = sel, sel["chosen"]
        rec.status = "ok" if sel["ok"] else "partial"
        if self.tracer is not None and self.tracer.enabled:
            if st is not None:
                self.tracer.event("selection", self.round, task=rec.task_id, prompt=st.prompt, reply=st.reply,
                                  ids=sel.get("ids"), chosen=sel["chosen"], fallback=sel.get("fallback"))
            self.tracer.decision(self.round, kept=f"{rec.task_id}/{rec.adopted}",
                                 incumbent_before=self.base_tree.short_id,
                                 incumbent_after=rec.adopted_tree.short_id if rec.adopted_tree else None,
                                 why=sel.get("why", ""), status=rec.status, dropped=sel.get("dropped"),
                                 kept_base=rec.kept_base(),
                                 note="H*_t = the adopted harness (selected by deliverable, not verified); the next "
                                      "task starts from H0 again")
        return rec

    def reflect(self) -> dict:
        """One reflector session over the task's whole improvement record D_t (no library)."""
        rec = self.rec
        pool = [(lab, rec.trees[lab], r) for lab, r in rec.rollouts.items() if not lab.startswith("base")]
        bases = [r for lab, r in rec.rollouts.items() if lab.startswith("base")]
        out, st = reflect(self.select_session, rec, task_text=self.text, rollouts=bases, pool=pool,
                          decisions=dict(self.decisions), slots=self.slots, obs_trunc=self.cfg.obs_trunc)
        rec.stages.append(st)
        rec.reflection = out
        if self.tracer is not None and self.tracer.enabled:
            self.tracer.event("reflection", self.round, task=rec.task_id, prompt=st.prompt, reply=st.reply,
                              experience=out.get("experience"), no_entry=out.get("no_entry"), error=out.get("error"))
        return out
