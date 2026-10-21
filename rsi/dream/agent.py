"""Object level, frozen during a run [paper:§3]: discovery task, locked evaluator, discovery agent, directions."""
from __future__ import annotations

import fnmatch
import json
import random
import re
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

    def render(self, max_chars: Optional[int] = 600) -> str:
        """Compact one-record view: proposal clipped to ``max_chars``, the error's informative end."""
        head = f"[round {self.round} {self.cell}{' dir=' + self.direction if self.direction else ''}] "
        res = f"score={self.score:.6g}" if self.score is not None and self.fail_class == "ok" else \
            f"FAILED ({self.fail_class}): {_error_gist(self.error)}"
        prop = (self.proposal or "").strip().replace("\n", " ")[:max_chars]
        return f"{head}{res}\n  proposal: {prop}"

    def render_dir(self, max_chars: Optional[int] = None) -> str:
        """The attempt directory as Listing 1 reads it [paper:App.B.1 L1:9]; ``max_chars`` clips visibly."""
        ok = self.score is not None and self.fail_class == "ok" and not self.error
        name = f"attempt_{self.cell.replace('/', '_').replace('.', '_')}/"
        tag = f" (branch direction: {self.direction})" if self.direction else ""
        prop = (self.proposal or "").strip() or "(empty)"
        if max_chars is not None and len(prop) > max_chars:
            prop = prop[:max_chars] + f" [... clipped by the calling system's history cap: {len(prop) - max_chars} " \
                                      "more characters not shown]"
        score = json.dumps({"score": self.score, "fail_class": self.fail_class}, default=float)
        when = f"live search {self.round}" if "/" in self.cell else f"decision round {self.round}"
        out = [f"{name}  [{when}]{tag}", "  proposal.md:", _indent(prop, 4), f"  eval/score.json: {score}"]
        if not ok:
            err = str(self.error or "(no error text)")
            if max_chars is not None:
                err = _error_gist(err)
            out += ["  error.txt:", _indent(err, 4)]
        return "\n".join(out)


def _indent(text: str, n: int) -> str:
    pad = " " * n
    return "\n".join(pad + line for line in str(text).splitlines() or [""])


def _error_gist(error, n: int = 240) -> str:
    """The end of an error, where a traceback states its cause."""
    e = str(error or "").strip()
    return e if len(e) <= n else "..." + e[-n:]


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
    baseline_proposal: Optional[str] = None
    baseline_fail_class: str = "ok"
    baseline_error: Optional[str] = None
    history_note: str = ""


@dataclass
class AgentAttempt:
    artifact: Optional[Artifact]
    proposal: str = ""
    usage: Usage = field(default_factory=Usage)
    error: Optional[str] = None
    meta: dict = field(default_factory=dict)


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
    """A :class:`rsi.core.Domain` as a discovery task: score = mean task score on its decision split."""

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
        self.last_results: dict = {}

    @staticmethod
    def _decision_split(domain: Domain, split: Optional[str]) -> str:
        """``split`` or the first non-empty of evolve/train/val; sealed or empty splits raise."""
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
        self.last_results[artifact.id] = res
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


class DiscoveryAgent:
    """Frozen discovery agent: one call = one new candidate from a parent workspace."""

    def attempt(self, ctx: AttemptContext, *, seed: int) -> AgentAttempt:  # pragma: no cover
        raise NotImplementedError


# Listing 1 [paper:App.B.1], verbatim; build_instructions fills the $variables and renders the named dirs inline
EXPLORATION_PROMPT = """\
You must read every historical proposal before proposing or implementing a new solution.

$direction_guidance

Variables (`$node_dir`, `$history_dir`, `$baseline_dir`, `$eval_program`, `$problem_file`) are filled in by the calling system. `$node_dir` is your own attempt directory -- exclude it when scanning sibling `attempt_*/` dirs.

## 1. Read the complete history first

Before proposing anything, read every `proposal.md` under sibling `attempt_*/` dirs, `$history_dir`, and `$baseline_dir` in full -- not a sample, not just recent cycles or the current branch. For each, read its matching `eval/score.json` (and `error.txt` if it failed). Trust the measured result over what the proposal claims about itself.

## 2. Learn from both successes and failures

For every past attempt, note the mechanism and how it did. For failures, figure out *why*: a flawed core idea, or a good idea let down by a bug, bad parameters, or an implementation slip? Don't repeat the former. The latter is worth retrying -- but only once you've actually located the bug in the code (not just guessed from the proposal), and only with a specific fix in hand.

## 3. Don't converge into a local optimum

Look at the shape of what's been tried. If most attempts cluster around small variations of one mechanism with flattening returns, that's a local optimum - resist proposing another small tweak there. Deliberately favor a structurally different mechanism or an untried combination over a safer marginal refinement. Exploration diversity matters as much as the next incremental gain.

## 4. Propose and implement

The new idea must be a genuinely new mechanism, a new combination of previously-successful pieces, or a targeted fix to a specific bug found in step 2 - never a repeat or rename of something already tried. Implement it in `$eval_program`. Don't claim it compiles, is correct, or beats SOTA until it's actually evaluated.

## Files

Write only `$node_dir/proposal.md` (mechanism, evidence from history, why it's not a repeat, expected benefit/risk) and `$node_dir/$eval_program`. Everything else is read-only.

## Note:
Never execute pkill, kill, killall, or terminate unrelated processes.
"""

# appended to Listing 1 for a single-completion editor (no file tools)
WORKSPACE_VIEW = """\

---
# Files filled in by the calling system (rendered inline: this call has no file tools)

`$node_dir` = `{node_dir}` (your own attempt: it is not listed below). `$eval_program` = {eval_program}. `$problem_file` = `problem.md`. `$history_dir` = `history/`. `$baseline_dir` = `baseline/`.
{history_note}
## problem.md
{problem}

## Sibling `attempt_*/` dirs of this search: the branch you resume (oldest first)
{lineage}

## Sibling `attempt_*/` dirs of this search: other branches
{siblings}

## `history/`: every attempt of the earlier live searches
{history}

## `baseline/`
{baseline}

## Writing `$node_dir/proposal.md` in this call
Your reply's JSON header is `$node_dir/proposal.md`: "change" is a one-line mechanism summary and "hypothesis" is the proposal (mechanism, evidence from history, why it's not a repeat, expected benefit/risk). The files you return are `$node_dir/$eval_program`; `eval/` and `error.txt` are written by the evaluator.
"""


def _render_records(recs: Sequence[AttemptRecord], limit: Optional[int] = None, empty: str = "(none)",
                    max_chars: Optional[int] = None) -> str:
    """Every record (Listing 1: "not a sample"); a non-default ``limit`` keeps the newest and says so."""
    if not recs:
        return empty
    recs = list(recs)
    omitted = max(0, len(recs) - limit) if limit is not None else 0
    shown = recs[-limit:] if limit is not None else recs
    body = "\n".join(r.render_dir(max_chars) for r in shown)
    return (f"({omitted} older attempts omitted by the calling system's history cap)\n" if omitted else "") + body


class EditorAgent(DiscoveryAgent):
    """LLM discovery agent over any :class:`rsi.core.Editor` with the Listing-1 prompt."""

    def __init__(self, editor: Editor | LLM, *, editable: Optional[Sequence[str]] = None, role: str = "agent",
                 max_history: Optional[int] = None, max_proposal_chars: Optional[int] = None,
                 system: Optional[str] = None) -> None:
        self.editor = RewriteEditor(editor) if isinstance(editor, LLM) else editor
        self.editable = list(editable) if editable is not None else None
        self.role = role
        # Listing 1 reads every proposal in full: both caps are off by default and announced when set
        self.max_history = max_history
        self.max_proposal_chars = max_proposal_chars
        self.system = system or ("You are an expert research engineer running one attempt of an automated "
                                 "discovery search. Write correct, efficient code.")

    def build_instructions(self, ctx: AttemptContext) -> str:
        editable = list(self.editable or ctx.editable or [])
        prog = ", ".join(f"`{e}`" for e in editable) if editable else "the program files of the workspace below"
        mc = self.max_proposal_chars
        base = AttemptRecord("baseline", -1, -1, 0, ctx.baseline_proposal or "(the initial program: it has no "
                             "proposal.md)", ctx.baseline_score, ctx.baseline_fail_class, ctx.baseline_error)
        baseline = "\n".join(base.render_dir(mc).splitlines()[1:])  # no attempt_*/ header for baseline/
        notes = [ctx.history_note] if ctx.history_note else []
        if self.max_history is not None or mc is not None:
            notes.append("NOTE: this calling system caps what it shows"
                         + (f" to the {self.max_history} most recent attempts per section" if self.max_history else "")
                         + (f"{' and' if self.max_history else ''} clips each proposal.md at {mc} characters"
                            if mc is not None else "") + "; omissions are marked where they happen.")
        listing = EXPLORATION_PROMPT.replace("$direction_guidance", ctx.direction_guidance or "")
        return listing + WORKSPACE_VIEW.format(
            node_dir=f"attempt_b{ctx.branch}_a{ctx.attempt}/", eval_program=prog,
            history_note=("\n".join(notes) + "\n") if notes else "",
            problem=ctx.problem,
            lineage=_render_records(ctx.lineage, self.max_history,
                                    "(none yet: this branch starts from the initial workspace)", mc),
            siblings=_render_records(ctx.siblings, self.max_history, "(none)", mc),
            history=_render_records(ctx.history, self.max_history, "(none: this is the first live search)", mc),
            baseline=baseline,
        )

    def attempt(self, ctx: AttemptContext, *, seed: int) -> AgentAttempt:
        from .question import WORKSPACE_META, program_only

        ws = ctx.parent_workspace
        editable = self.editable or ctx.editable or [n for n in ws if n not in WORKSPACE_META]
        instructions = self.build_instructions(ctx)
        prop = self.editor.edit(ws, instructions, editable=editable, system=self.system, seed=seed, role=self.role)
        try:
            shown = self.editor.build_prompt(ws, instructions, None, editable) \
                if hasattr(self.editor, "build_prompt") else instructions
        except Exception:  # noqa: BLE001 - auditing must never break an attempt
            shown = instructions
        audit = {"prompt": shown, "reply": prop.raw, "change": prop.change, "hypothesis": prop.hypothesis,
                 "components": list(prop.components), "blocked": list(prop.blocked_files)}
        if not prop.ok:
            return AgentAttempt(None, prop.change or "", prop.usage, prop.error or "no candidate", audit)
        art, cleaned = strip_reply_terminators(prop.artifact, editable)
        audit["sanitized"] = cleaned
        text = f"# {prop.change}\n\n{prop.hypothesis}".strip()
        return AgentAttempt(program_only(art), text, prop.usage, None, audit)


_TERMINATOR = re.compile(r"^\s*(```[\w+-]*|={3,}(\s*(END|EOF)[^=]*={0,})?)\s*$", re.I)
_OPEN_FENCE = re.compile(r"^\s*```[\w+-]*\s*$")


def strip_reply_terminators(art: Artifact, editable: Sequence[str]) -> tuple[Artifact, list[str]]:
    """Drop reply-format lines (fences, ``===`` terminators) left at the edges of editable ``.py`` files."""
    fixed: dict[str, str] = {}
    for name in art:
        if not name.endswith(".py") or not any(fnmatch.fnmatch(name, pat) for pat in editable):
            continue
        orig = art[name].rstrip("\n").split("\n")
        lines = list(orig)
        n0 = len(lines)
        while lines and (not lines[-1].strip() or _TERMINATOR.match(lines[-1])):
            lines.pop()
        changed = len(lines) < n0 and any(_TERMINATOR.match(l) for l in orig[len(lines):])
        if not changed:
            lines = list(orig)
        head = 0
        while head < len(lines) and not lines[head].strip():
            head += 1
        if head < len(lines) and _OPEN_FENCE.match(lines[head]):
            lines = lines[head + 1:]
            changed = True
        if changed:
            fixed[name] = "\n".join(lines) + "\n"
    return (art.with_files(fixed) if fixed else art), sorted(fixed)


class ParametricAgent(DiscoveryAgent):
    """Deterministic mock agent: ``mutate(parent_program, rng, ctx) -> (program, proposal)``."""

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


class DirectionProvider:
    """Gives new roots the direction classes in a per-round shuffled cycle, or advised ones (E5 guidance)."""

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
            out[b] = {"direction": order[b % len(order)]}
            if guidance and guidance.get("focus") and rng.random() < float(guidance.get("strength", 0.8)):
                focus = list(guidance["focus"])
                out[b] = {"direction": focus[b % len(focus)], "advised": True}
        return out


def record_of(node, direction: Optional[str] = None) -> AttemptRecord:
    return AttemptRecord(node.id, node.branch, node.attempt, node.round, node.proposal, node.score, node.fail_class,
                         node.error, direction or (node.tags or {}).get("direction"))


def history_records(worlds: Sequence, max_records: Optional[int] = None) -> list[AttemptRecord]:
    """``H_{t-1}`` as attempt records (newest last) tagged with their live round; ``max_records`` keeps the newest."""
    out: list[AttemptRecord] = []
    for w in worlds:
        rnd = int(w.meta.get("round", 0))
        for n in w.non_root():
            r = record_of(n)
            r.round = rnd
            r.cell = f"t{rnd}/{n.id}"
            out.append(r)
    return out[-max_records:] if max_records is not None else out
