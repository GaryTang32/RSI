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
        """Compact one-record view (proposal clipped to ``max_chars``; the error's informative end)."""
        head = f"[round {self.round} {self.cell}{' dir=' + self.direction if self.direction else ''}] "
        res = f"score={self.score:.6g}" if self.score is not None and self.fail_class == "ok" else \
            f"FAILED ({self.fail_class}): {_error_gist(self.error)}"
        prop = (self.proposal or "").strip().replace("\n", " ")
        prop = prop[:max_chars] if max_chars is not None else prop
        return f"{head}{res}\n  proposal: {prop}"

    def render_dir(self, max_chars: Optional[int] = None) -> str:
        """The attempt directory as Listing 1 has the agent read it: ``proposal.md`` in full,
        ``eval/score.json`` and ``error.txt`` when it failed [paper:App.B.1 L1:9]. With
        ``max_chars`` (a non-default history cap) the proposal is clipped and says so."""
        ok = self.score is not None and self.fail_class == "ok" and not self.error
        name = f"attempt_{self.cell.replace('/', '_').replace('.', '_')}/"
        tag = f" (branch direction: {self.direction})" if self.direction else ""
        prop = (self.proposal or "").strip() or "(empty)"
        if max_chars is not None and len(prop) > max_chars:
            prop = prop[:max_chars] + f" [... clipped by the calling system's history cap: {len(prop) - max_chars} " \
                                      "more characters not shown]"
        score = json.dumps({"score": self.score, "fail_class": self.fail_class}, default=float)
        when = f"live search {self.round}" if "/" in self.cell else f"decision round {self.round}"
        out = [f"{name}  [{when}]{tag}", "  proposal.md:", _indent(prop, 4),
               f"  eval/score.json: {score}"]
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
    """The informative end of an error: a Python traceback states the cause (and the offending
    line) at its END, so a head-only clip hid e.g. ``SyntaxError`` at a stray ``===`` line from
    the agent (live validation run, 2026-09-25) and it blamed the idea instead of the slip."""
    e = str(error or "").strip()
    if len(e) <= n:
        return e
    return "..." + e[-n:]


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
    #: ``$baseline_dir`` of Listing 1: the baseline's proposal.md (None: the initial program has none)
    #: and its evaluation (fail_class / error.txt)
    baseline_proposal: Optional[str] = None
    baseline_fail_class: str = "ok"
    baseline_error: Optional[str] = None
    #: history caps actually applied by the calling system (disclosed in the prompt; None = none)
    history_note: str = ""


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
        #: audit only (rsi.trace): the last EvalResult per artifact id (per-task + raw trial scores)
        self.last_results: dict = {}

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


# ---------------------------------------------------------------------------- agents
class DiscoveryAgent:
    """Frozen discovery agent: one call = one new candidate from a parent workspace."""

    def attempt(self, ctx: AttemptContext, *, seed: int) -> AgentAttempt:  # pragma: no cover
        raise NotImplementedError


#: Listing 1 [paper:App.B.1], verbatim. The ``$variables`` are "filled in by the calling system"
#: (L1:5): :meth:`EditorAgent.build_instructions` substitutes them and - because a single-completion
#: editor has no file tools - renders the directories they name (the sibling ``attempt_*/`` dirs,
#: ``$history_dir``, ``$baseline_dir`` and ``$problem_file``) inline after the listing.
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

#: what the calling system appends to Listing 1 for a single-completion editor (no file tools):
#: the directories the listing names, rendered inline, and where proposal.md goes in the reply
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
    """Every record (Listing 1: "not a sample"); with ``limit`` (a non-default cap) only the most
    recent ``limit``, and the omission is stated."""
    if not recs:
        return empty
    recs = list(recs)
    omitted = max(0, len(recs) - limit) if limit is not None else 0
    shown = recs[-limit:] if limit is not None else recs
    body = "\n".join(r.render_dir(max_chars) for r in shown)
    return (f"({omitted} older attempts omitted by the calling system's history cap)\n" if omitted else "") + body


class EditorAgent(DiscoveryAgent):
    """LLM discovery agent over any :class:`rsi.core.Editor` (Listing-1 prompt).

    ``editor`` may be a :class:`rsi.core.RewriteEditor` (one completion returns
    whole files) or an :class:`rsi.core.AgentEditor` (``claude -p`` coding agent in
    a scratch copy of the workspace). ``editable`` restricts writes to the program
    files (default: every file except the workspace meta files).
    """

    def __init__(self, editor: Editor | LLM, *, editable: Optional[Sequence[str]] = None, role: str = "agent",
                 max_history: Optional[int] = None, max_proposal_chars: Optional[int] = None,
                 system: Optional[str] = None) -> None:
        self.editor = RewriteEditor(editor) if isinstance(editor, LLM) else editor
        self.editable = list(editable) if editable is not None else None
        self.role = role
        # Listing 1: "read every proposal.md ... in full -- not a sample". Both caps are OFF by default;
        # set them only to bound prompt length (a documented, non-default deviation that the prompt
        # then states: omitted records and clipped proposals are announced, never silent)
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
        baseline = "\n".join(base.render_dir(mc).splitlines()[1:])          # no attempt_*/ header for baseline/
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
        prop = self.editor.edit(ws, instructions, editable=editable, system=self.system,
                                seed=seed, role=self.role)
        # audit record (rsi.trace): the exact prompt the model saw and its raw reply
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
    """Drop reply-format lines that ``parse_file_blocks`` leaves in a file block: trailing ones (a
    closing fence or a bare ``===`` / ``=== END ===`` terminator, when the model fences only the end
    of a file) and a leading opening fence (```` ```python ````, when the closing fence is not the
    block's last line or is missing). Such a line is never valid code, and keeping it turned a
    sound candidate into a SyntaxError (the live validation run of 2026-09-25: 6 of 12
    round-1 attempts). Only such boundary lines of the editable ``.py`` files are touched."""
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
        # a LEADING opening fence (```python) is left in place by parse_file_blocks whenever the
        # block's closing fence is not the very last line (e.g. "```\n===" or no closing fence at
        # all); it is never valid Python either (stage-B audit, 2026-09-25)
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


def history_records(worlds: Sequence, max_records: Optional[int] = None) -> list[AttemptRecord]:
    """``H_{t-1}`` as attempt records (newest last), each tagged with its live round. Every record by
    default (Listing 1: "not just recent cycles"); ``max_records`` keeps only the newest ones."""
    out: list[AttemptRecord] = []
    for w in worlds:
        rnd = int(w.meta.get("round", 0))
        for n in w.non_root():
            r = record_of(n)
            r.round = rnd
            r.cell = f"t{rnd}/{n.id}"
            out.append(r)
    return out[-max_records:] if max_records is not None else out


def score_json(ws: Artifact) -> dict:
    try:
        return json.loads(ws.get("eval/score.json") or "{}")
    except json.JSONDecodeError:
        return {}

