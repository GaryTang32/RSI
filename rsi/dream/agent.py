"""Object level (frozen during a run): the discovery task, its locked evaluator,
the discovery agent and the direction provider.

"Only the exploration-policy code changes; the underlying models, evaluator, and
execution interfaces remain fixed" [paper:§3 p.4].

* :class:`EvalOutcome` - what the locked evaluator reports (score, validity,
  ``fail_class``, error, n_valid/n_total, diagnostics, seconds).
* :class:`DiscoveryTask` - problem statement + seed program + locked ``evaluate``;
  :class:`DomainTask` turns ANY :class:`rsi.core.Domain` into one (score = mean
  score S on the ``evolve`` split via :class:`rsi.core.Evaluator`).
* :class:`AttemptContext` - what one attempt reads: the parent's workspace, its
  branch lineage, sibling attempts of this rollout, history ``H_{t-1}``, the
  baseline, and the ``$direction_guidance`` text [paper:App.B.1].
* :class:`EditorAgent` - an LLM discovery agent (any :class:`rsi.core.Editor`:
  ``RewriteEditor`` over any LLM or ``AgentEditor`` = ``claude -p`` with file tools)
  driven by the Listing-1 exploration prompt.
* :class:`ParametricAgent` - a deterministic mock agent built from a domain's
  ``mutate(parent, rng, ctx)`` function.
* :class:`DirectionProvider` - assigns each planned root a direction tag
  ("the direction provider assigns those new roots their directions").
"""
from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass, field
from typing import Callable, Optional, Sequence

from ..core.artifact import Artifact
from ..core.domain import Domain
from ..core.editors import Editor, RewriteEditor
from ..core.evaluate import Evaluator
from ..core.llm import LLM, Usage


@dataclass
class EvalOutcome:
    score: Optional[float]
    evaluated: bool = True
    valid: bool = True
    fail_class: str = "ok"
    error: Optional[str] = None
    n_valid: Optional[int] = None
    n_total: Optional[int] = None
    diagnostics: dict = field(default_factory=dict)
    seconds: float = 0.0


@dataclass
class AttemptRecord:
    """One past attempt as the agent sees it (``proposal.md`` + ``eval/score.json`` + ``error.txt``)."""

    cell: str
    branch: int
    attempt: int
    round: int
    proposal: str
    score: Optional[float]
    fail_class: str = "ok"
    error: Optional[str] = None
    direction: Optional[str] = None

    def render(self, max_chars: int = 600) -> str:
        head = f"[round {self.round} {self.cell}{' dir=' + self.direction if self.direction else ''}] "
        res = f"score={self.score:.6g}" if self.score is not None and self.fail_class == "ok" else \
            f"FAILED ({self.fail_class}): {str(self.error)[:200]}"
        prop = (self.proposal or "").strip().replace("\n", " ")[:max_chars]
        return f"{head}{res}\n  proposal: {prop}"


@dataclass
class AttemptContext:
    problem: str
    parent_id: str
    parent_workspace: Artifact
    parent_score: Optional[float]
    branch: int
    attempt: int
    round: int
    lineage: list[AttemptRecord] = field(default_factory=list)
    siblings: list[AttemptRecord] = field(default_factory=list)
    history: list[AttemptRecord] = field(default_factory=list)
    baseline_score: Optional[float] = None
    direction: dict = field(default_factory=dict)
    direction_guidance: str = ""
    editable: Optional[Sequence[str]] = None


@dataclass
class AgentAttempt:
    artifact: Optional[Artifact]
    proposal: str = ""
    usage: Usage = field(default_factory=Usage)
    error: Optional[str] = None
    meta: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------- tasks
class DiscoveryTask:
    """A discovery problem with a locked evaluator. Larger scores are better."""

    name = "task"

    def describe(self) -> str:
        return self.name

    def seed_artifact(self) -> Artifact:
        raise NotImplementedError

    def evaluate(self, artifact: Artifact, *, seed: int = 0) -> EvalOutcome:
        raise NotImplementedError

    def directions(self) -> list[str]:
        """Direction classes the direction provider may assign to new roots."""
        return []

    def editable(self) -> Optional[list[str]]:
        return None


class DomainTask(DiscoveryTask):
    """Any :class:`rsi.core.Domain` as a discovery task.

    The score of a program is the mean task score ``S`` of the domain's ``evolve``
    split (or ``train`` / ``val`` when it has no ``evolve``; never a sealed split;
    ``k`` trials) with the frozen task LLM - graded only inside ``Domain.grade``. ``fail_class`` is ``"ok"`` when at least one trial ran without
    an execution error, ``"compile_other"`` when all crashed (repairable), and
    ``"env_error"`` when every trial hit an ``infra:`` backend error.
    """

    def __init__(self, domain: Domain, seed: Artifact, llm_task: Optional[LLM] = None, *, split: Optional[str] = None,
                 k: int = 1, workers: int = 4, directions: Sequence[str] = (), editable: Optional[list[str]] = None,
                 cache_dir: Optional[str] = None) -> None:
        self.domain = domain
        self._seed = seed
        self.name = getattr(domain, "name", "domain")
        self.split, self.k = self._decision_split(domain, split), k
        self.evaluator = Evaluator(domain, llm_task, workers=workers, cache_dir=cache_dir)
        self._directions = list(directions)
        self._editable = editable

    @staticmethod
    def _decision_split(domain: Domain, split: Optional[str]) -> str:
        """The split the discovery score is computed on: ``split`` if given, else the first
        non-empty decision split (``evolve``, then ``train``, then ``val``). Sealed splits
        (holdout/ood/test) can never be chosen, and an empty split raises instead of
        silently scoring every program 0 as a "successful" evaluation."""
        splits = domain.tasks.splits
        if split is None:
            split = next((s for s in ("evolve", "train", "val") if splits.get(s)), None)
            if split is None:
                raise ValueError(f"domain {getattr(domain, 'name', '?')!r} has no non-empty decision split "
                                 f"(evolve/train/val); splits: {sorted(splits)}")
        if domain.tasks.is_sealed(split):
            raise ValueError(f"split {split!r} is sealed: discovery decisions may not be made on it")
        if not splits.get(split):
            raise ValueError(f"split {split!r} of domain {getattr(domain, 'name', '?')!r} is empty or missing "
                             f"(splits: {sorted(splits)})")
        return split

    def describe(self) -> str:
        return self.domain.describe()

    def seed_artifact(self) -> Artifact:
        return self._seed

    def directions(self) -> list[str]:
        return list(self._directions)

    def editable(self):
        return self._editable

    def evaluate(self, artifact: Artifact, *, seed: int = 0) -> EvalOutcome:
        t0 = time.time()
        res = self.evaluator.evaluate(artifact, self.split, self.k)
        n_total = res.n_trials
        errs = [t for trs in res.trials.values() for t in trs if t.error]
        infra = [t for t in errs if str(t.error).startswith("infra:")]
        n_valid = n_total - len(errs)
        if n_total and len(infra) == n_total:
            fc, err = "env_error", str(infra[0].error)[:300]
        elif n_total and n_valid == 0:
            fc, err = "compile_other", str(errs[0].error)[:300]
        else:
            fc, err = "ok", None
        worst = res.worst(2)
        diag = {"S": res.score, "C": res.cost, "error_rate": res.error_rate,
                "feedback": [f"{t.task_id}: {t.feedback[:200]}" for t in worst]}
        return EvalOutcome(res.score if fc == "ok" else 0.0, True, fc == "ok" and res.error_rate == 0, fc, err,
                           n_valid, n_total, diag, time.time() - t0)


# ---------------------------------------------------------------------------- agents
class DiscoveryAgent:
    """Frozen discovery agent: one call = one new candidate from a parent workspace."""

    def attempt(self, ctx: AttemptContext, *, seed: int) -> AgentAttempt:  # pragma: no cover
        raise NotImplementedError


#: Listing 1 [paper:App.B.1], adapted to a single-completion editor: the "directories"
#: are rendered inline and the files to write are the program files + a proposal.
EXPLORATION_PROMPT = """\
You must read every historical proposal before proposing or implementing a new solution.

{direction_guidance}

## Problem
{problem}

## 1. Read the complete history first
Below are every sibling attempt of the current search, the completed history of earlier
rounds, and the baseline - in full, not a sample. Trust the measured result over what a
proposal claims about itself.

### This branch (the workspace you resume; oldest first)
{lineage}

### Sibling attempts in this search
{siblings}

### History of earlier rounds
{history}

### Baseline
score = {baseline}

## 2. Learn from both successes and failures
For every past attempt, note the mechanism and how it did. For failures, figure out *why*:
a flawed core idea, or a good idea let down by a bug, bad parameters, or an implementation
slip? Don't repeat the former. The latter is worth retrying - but only once you've actually
located the bug in the code, and only with a specific fix in hand.

## 3. Don't converge into a local optimum
If most attempts cluster around small variations of one mechanism with flattening returns,
that's a local optimum - resist proposing another small tweak there. Deliberately favor a
structurally different mechanism or an untried combination over a safer marginal refinement.
Exploration diversity matters as much as the next incremental gain.

## 4. Propose and implement
The new idea must be a genuinely new mechanism, a new combination of previously-successful
pieces, or a targeted fix to a specific bug found in step 2 - never a repeat or rename of
something already tried. Implement it in the program files of the current workspace (shown
below; the parent's evaluation is in eval/score.json). Don't claim it compiles, is correct, or
beats SOTA until it's actually evaluated.

## Files
In the JSON header, "change" is a one-line mechanism summary and "hypothesis" is the proposal
(mechanism, evidence from history, why it's not a repeat, expected benefit/risk). Only the
program files are yours to write; proposal.md, eval/ and error.txt are written by the system.
"""


def _render_records(recs: Sequence[AttemptRecord], limit: int, empty: str = "(none)") -> str:
    if not recs:
        return empty
    recs = list(recs)
    omitted = max(0, len(recs) - limit)
    body = "\n".join(r.render() for r in recs[-limit:])
    return (f"({omitted} older attempts omitted)\n" if omitted else "") + body


class EditorAgent(DiscoveryAgent):
    """LLM discovery agent over any :class:`rsi.core.Editor` (Listing-1 prompt).

    ``editor`` may be a :class:`rsi.core.RewriteEditor` (one completion returns
    whole files) or an :class:`rsi.core.AgentEditor` (``claude -p`` coding agent in
    a scratch copy of the workspace). ``editable`` restricts writes to the program
    files (default: every file except the workspace meta files).
    """

    def __init__(self, editor: Editor | LLM, *, editable: Optional[Sequence[str]] = None, role: str = "agent",
                 max_history: int = 30, system: Optional[str] = None) -> None:
        self.editor = RewriteEditor(editor) if isinstance(editor, LLM) else editor
        self.editable = list(editable) if editable is not None else None
        self.role = role
        self.max_history = max_history
        self.system = system or ("You are an expert research engineer running one attempt of an automated "
                                 "discovery search. Write correct, efficient code.")

    def build_instructions(self, ctx: AttemptContext) -> str:
        return EXPLORATION_PROMPT.format(
            direction_guidance=ctx.direction_guidance or "",
            problem=ctx.problem,
            lineage=_render_records(ctx.lineage, self.max_history, "(this branch starts from the initial workspace)"),
            siblings=_render_records(ctx.siblings, self.max_history),
            history=_render_records(ctx.history, self.max_history),
            baseline=f"{ctx.baseline_score:.6g}" if ctx.baseline_score is not None else "n/a",
        )

    def attempt(self, ctx: AttemptContext, *, seed: int) -> AgentAttempt:
        from .question import WORKSPACE_META, program_only

        ws = ctx.parent_workspace
        editable = self.editable or ctx.editable or [n for n in ws if n not in WORKSPACE_META]
        prop = self.editor.edit(ws, self.build_instructions(ctx), editable=editable, system=self.system,
                                seed=seed, role=self.role)
        if not prop.ok:
            return AgentAttempt(None, prop.change or "", prop.usage, prop.error or "no candidate")
        text = f"# {prop.change}\n\n{prop.hypothesis}".strip()
        return AgentAttempt(program_only(prop.artifact), text, prop.usage, None,
                            {"components": prop.components, "blocked": prop.blocked_files})


class ParametricAgent(DiscoveryAgent):
    """Deterministic mock agent: ``mutate(parent_program, rng, ctx) -> (program, proposal)``.

    ``calls_usage`` is the simulated usage of one call (so the cost meter counts calls
    exactly like a real agent). Exceptions inside ``mutate`` become agent errors.
    """

    def __init__(self, mutate: Callable[[Artifact, random.Random, AttemptContext], tuple[Artifact, str]],
                 *, name: str = "mock-agent", tokens_per_call: int = 0) -> None:
        self.mutate = mutate
        self.name = name
        self.tokens_per_call = tokens_per_call

    def attempt(self, ctx: AttemptContext, *, seed: int) -> AgentAttempt:
        from .question import program_only

        rng = random.Random(seed)
        usage = Usage(1, self.tokens_per_call, 0, 0.0, 0.0)
        try:
            prog, proposal = self.mutate(program_only(ctx.parent_workspace), rng, ctx)
        except Exception as e:  # noqa: BLE001 - an agent slip is a failed attempt, not a crash
            return AgentAttempt(None, "", usage, f"{type(e).__name__}: {e}")
        return AgentAttempt(prog, proposal, usage)


# ------------------------------------------------------------------------ directions
class DirectionProvider:
    """Assigns direction tags to the roots of a new live grid.

    Default: cycle through the task's direction classes in a per-round rotated order
    (diverse coverage). With ``guidance`` set (the E5 "written advice" ablation), each
    root adopts one of the advised directions with probability ``strength``.
    """

    def __init__(self, classes: Sequence[str] = (), seed: int = 0) -> None:
        self.classes = list(classes)
        self.seed = seed

    def assign(self, n_branches: int, round_index: int, guidance: Optional[dict] = None) -> dict[int, dict]:
        if not self.classes:
            return {b: {} for b in range(n_branches)}
        rng = random.Random(f"{self.seed}|dirs|{round_index}")
        order = list(self.classes)
        rng.shuffle(order)
        out = {}
        for b in range(n_branches):
            d = order[b % len(order)]
            tags = {"direction": d}
            if guidance and guidance.get("focus"):
                if rng.random() < float(guidance.get("strength", 0.8)):
                    focus = list(guidance["focus"])
                    d = focus[b % len(focus)]
                    tags = {"direction": d, "advised": True}
            out[b] = tags
        return out


def record_of(node, direction: Optional[str] = None) -> AttemptRecord:
    return AttemptRecord(node.id, node.branch, node.attempt, node.round, node.proposal, node.score,
                         node.fail_class, node.error,
                         direction or (node.tags or {}).get("direction"))


def history_records(worlds: Sequence, max_records: int = 200) -> list[AttemptRecord]:
    """``H_{t-1}`` as attempt records (newest last), each tagged with its live round."""
    out: list[AttemptRecord] = []
    for w in worlds:
        rnd = int(w.meta.get("round", 0))
        for n in w.non_root():
            r = record_of(n)
            r.round = rnd
            r.cell = f"t{rnd}/{n.id}"
            out.append(r)
    return out[-max_records:]


def score_json(ws: Artifact) -> dict:
    try:
        return json.loads(ws.get("eval/score.json") or "{}")
    except json.JSONDecodeError:
        return {}

