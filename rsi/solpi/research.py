"""The SoL-Pi auto-research protocol pieces (spec B3.1 / B9.1).

* :class:`Idea` / :class:`IdeaPool` - breadth: a pool of harness directions from
  six proposal families (Context, Progress, Tools, Delegation, pRompt & policy,
  iMprovement & evaluation), ranked by :func:`oracle_estimate` - "estimates
  opportunity from existing trajectories before rollout budget";
* :func:`analyze` (map) / :func:`reduce_findings` (reduce) - per-trajectory analysis
  of repeated actions, context growth, large observations and sparse diagnostics;
* :class:`Lineage` - depth: one disposable Karpathy-style loop per idea::

      rollouts -> map-reduce analysis -> propose ONE mechanism -> implement (Ralph loop
      until the exit check passes) -> independent review -> in-trajectory validation on
      the training screen -> dual gate -> freeze | route back (fix / new rollouts) | abandon

  A lineage only ever sees training-split results; it never holds the firewall.
* :func:`compose` - merge surviving mechanisms into one harness (JSON-config union
  for SoL-Pi harnesses, per-file 3-way merge for other artifacts).
"""
from __future__ import annotations

import difflib
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Protocol, Sequence

import numpy as np

from ..core.artifact import Artifact
from ..core.domain import Trial
from ..core.evaluate import Evaluator
from ..core.ledger import Ledger, Node
from ..core.llm import Usage
from .gate import DualGate, GateResult, Metrics, metrics_from_eval

FAMILY_CODES = {"C": "Context", "P": "Progress", "T": "Tools", "D": "Delegation", "R": "Prompt & policy",
                "M": "Improvement & evaluation"}


@dataclass
class Idea:
    id: str                      # e.g. "C23"
    family: str                  # one of FAMILY_CODES
    title: str
    mechanism: str = ""          # registry name the mock implementer maps it to ("" = free-form, LLM implements)
    grid: list[dict] = field(default_factory=lambda: [{}])   # variants, most aggressive first
    oracle: str = "none"         # oracle statistic name
    kind: str = "general"        # general | trick | do_less | dud  (ground truth, never shown to the gate)
    notes: str = ""


class IdeaPool:
    def __init__(self, ideas: Sequence[Idea]) -> None:
        self.ideas = list(ideas)

    def select(self, n: int, estimates: dict[str, float]) -> list[Idea]:
        ranked = sorted(self.ideas, key=lambda i: (-estimates.get(i.id, 0.0), i.id))
        return ranked[:n]

    def by_family(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for i in self.ideas:
            out[i.family] = out.get(i.family, 0) + 1
        return out


# ------------------------------------------------------------------ oracle + map-reduce
def oracle_estimate(idea: Idea, trials: Sequence[Trial]) -> float:
    """Share of avoidable work the idea targets, from base trajectories (``Trial.meta['oracle']``)."""
    rows = [t.meta.get("oracle") for t in trials if isinstance(t.meta.get("oracle"), dict)]
    if not rows or idea.oracle == "none":
        return 0.05
    tot = sum(r.get("input_tokens", 0) for r in rows) or 1
    if idea.oracle == "adjacent_edit_command":
        tr = sum(r.get("transitions", 0) for r in rows) or 1
        return sum(r.get("adjacent_edit_command", 0) for r in rows) / tr
    if idea.oracle in ("replayed_large_outputs", "diagnostic_log_tokens", "archivable_context", "tail_of_outputs",
                       "prompt_tokens", "verification_tokens"):
        return sum(r.get(idea.oracle, 0) for r in rows) / tot
    return 0.05


def analyze(trial: Trial) -> dict:
    """Map step: findings for one trajectory."""
    m, o = trial.meta or {}, (trial.meta or {}).get("oracle", {}) or {}
    return {"task": trial.task_id, "family": trial.family, "score": trial.score, "tokens": trial.tokens,
            "requests": trial.steps, "repeated_actions": o.get("repeated_commands", 0),
            "context_growth": m.get("max_context", 0) / max(1, trial.steps),
            "large_observations": o.get("n_large_outputs", 0), "sparse_diagnostics": o.get("n_diag_outputs", 0),
            "adjacent_edit_command": o.get("adjacent_edit_command", 0)}


def reduce_findings(findings: Sequence[dict]) -> dict:
    """Reduce step: aggregate evidence across trajectories."""
    if not findings:
        return {}
    keys = ("score", "tokens", "requests", "repeated_actions", "context_growth", "large_observations",
            "sparse_diagnostics", "adjacent_edit_command")
    out = {k: float(np.mean([f.get(k, 0) for f in findings])) for k in keys}
    out["n"] = len(findings)
    out["worst"] = sorted(findings, key=lambda f: (f["score"], -f["tokens"]))[:3]
    return out


# ------------------------------------------------------------------ roles
@dataclass
class MechanismProposal:
    artifact: Optional[Artifact]
    change: str = ""
    variant: int = 0
    usage: Usage = field(default_factory=Usage)
    error: Optional[str] = None
    meta: dict = field(default_factory=dict)


class MechanismProposer(Protocol):
    def propose(self, idea: Idea, base: Artifact, evidence: dict, history: list[dict]) -> MechanismProposal: ...
    def fix(self, idea: Idea, proposal: MechanismProposal, error: str) -> MechanismProposal: ...


class Reviewer(Protocol):
    def review(self, idea: Idea, base: Artifact, cand: Artifact) -> tuple[bool, str]: ...


class SmokeReviewer:
    """Behavioural-contract review without an LLM: the candidate must run a smoke task
    without crashing, keep the base harness files other than the mechanism config /
    code, and contain no references to held-out material (denylist)."""

    def __init__(self, domain, llm, forbidden: Sequence[str] = ("holdout", "heldout", "ood/", "test.json",
                                                                 "datalookup")) -> None:
        self.domain, self.llm, self.forbidden = domain, llm, [f.lower() for f in forbidden]

    def review(self, idea, base, cand):
        diff = base.diff(cand).lower()
        added = "\n".join(l for l in diff.splitlines() if l.startswith("+") and not l.startswith("+++"))
        for f in self.forbidden:
            if f in added:
                return False, f"contract: references held-out material ({f!r})"
        err = self.domain.smoke(cand, self.llm)
        return (err is None), (err or "ok")


def implement(proposer: MechanismProposer, idea: Idea, prop: MechanismProposal, exit_check: Callable[[Artifact],
              Optional[str]], max_iters: int = 3) -> tuple[MechanismProposal, list[str]]:
    """Ralph loop: keep implementing until the explicit exit check passes (or give up)."""
    errors = []
    for _ in range(max_iters):
        if prop.artifact is None:
            errors.append(prop.error or "no artifact")
        else:
            err = exit_check(prop.artifact)
            if err is None:
                return prop, errors
            errors.append(err)
        prop = proposer.fix(idea, prop, errors[-1])
    prop.error = prop.error or f"exit check failed: {errors[-1] if errors else '?'}"
    return prop, errors


# ------------------------------------------------------------------ lineage
@dataclass
class FrozenCandidate:
    name: str
    idea: Idea
    artifact: Artifact
    metrics: Metrics
    gate: GateResult
    evidence: dict
    iterations: int


@dataclass
class LineageResult:
    idea: Idea
    frozen: Optional[FrozenCandidate]
    iterations: list[dict]
    usage: Usage = field(default_factory=Usage)


class Lineage:
    """One disposable auto-research loop for one idea (training split only)."""

    def __init__(self, idea: Idea, *, evaluator: Evaluator, gate: DualGate, proposer: MechanismProposer,
                 reviewer: Reviewer, base: Artifact, base_metrics: Metrics, screen_split: str = "evolve",
                 rollout_tasks_per_family: int = 2, k: int = 1, max_iters: int = 4, ralph_max: int = 3,
                 workdir: Optional[Path] = None, ledger: Optional[Ledger] = None, sweep: bool = False) -> None:
        self.idea, self.ev, self.gate, self.proposer, self.reviewer = idea, evaluator, gate, proposer, reviewer
        self.base, self.base_metrics = base, base_metrics
        self.split, self.k, self.max_iters, self.ralph_max = screen_split, k, max_iters, ralph_max
        self.rollout_n = rollout_tasks_per_family
        self.workdir = workdir
        self.ledger = ledger
        self.sweep = sweep
        fams = gate.spec.families
        tasks = evaluator.domain.tasks.split(screen_split)
        self.screen = [t for t in tasks if fams is None or t.family in fams]

    def _rollout_tasks(self) -> list:
        out, per = [], {}
        for t in self.screen:
            if per.get(t.family, 0) < self.rollout_n:
                out.append(t)
                per[t.family] = per.get(t.family, 0) + 1
        return out

    def run(self) -> LineageResult:
        idea = self.idea
        history: list[dict] = []
        current = self.base
        usage = Usage()
        parent = None
        passing: list[FrozenCandidate] = []
        for it in range(self.max_iters):
            # 01 rollouts + 02 map-reduce analysis
            ro = self.ev.evaluate(current, self._rollout_tasks(), k=1, label="rollouts")
            evidence = reduce_findings([analyze(t) for trs in ro.trials.values() for t in trs])
            # 03 proposal (one mechanism) + 04 implementation (Ralph loop)
            prop = self.proposer.propose(idea, self.base, evidence, history)
            usage = usage + prop.usage
            prop, ralph_errors = implement(self.proposer, idea, prop, lambda a: self.ev.domain.smoke(a, self.ev.llm),
                                           self.ralph_max)
            row: dict[str, Any] = {"iteration": it, "change": prop.change, "variant": prop.variant,
                                   "ralph_errors": ralph_errors}
            if prop.artifact is None or prop.error:
                row.update(stage="implementation", outcome="abandoned", error=prop.error)
                history.append(row)
                self._log(row, None, parent)
                if prop.meta.get("exhausted"):
                    break
                continue
            # 05 independent review
            ok, why = self.reviewer.review(idea, self.base, prop.artifact)
            if not ok:
                row.update(stage="review", outcome="rejected", error=why)
                history.append(row)
                self._log(row, prop.artifact, parent)
                continue
            # 06 in-trajectory validation on the training screen + dual gate
            vr = self.ev.evaluate(prop.artifact, self.screen, k=self.k, label="screen")
            m = metrics_from_eval(vr)
            g = self.gate.accept(self.base_metrics, m)
            row.update(stage="validation", outcome="frozen" if g.accept else "gate_failed", gate=g.to_json(),
                       metrics=m.to_json())
            history.append(row)
            node = self._log(row, prop.artifact, parent, m)
            parent = node.id if node else parent
            if g.accept:
                fc = FrozenCandidate(f"{idea.id}:{prop.change}", idea, prop.artifact, m, g, evidence, it + 1)
                if not self.sweep:
                    return LineageResult(idea, fc, history, usage)
                passing.append(fc)
            current = prop.artifact
        if passing:
            # "among candidates that pass the capability floor, the loop retains nondominated results"
            from .gate import nondominated
            nd = nondominated([(f, f.metrics) for f in passing], self.gate.spec.efficiency)
            best = min(nd, key=lambda f: (f.metrics.agg[self.gate.spec.efficiency[0]], -f.metrics.agg["score"]))
            return LineageResult(idea, best, history, usage)
        return LineageResult(idea, None, history, usage)

    def _log(self, row: dict, art: Optional[Artifact], parent: Optional[str], m: Optional[Metrics] = None):
        if self.ledger is None:
            return None
        n = Node(id=f"sp_{self.idea.id}_{row['iteration']}", parent=parent, round=row["iteration"], kind="lineage",
                 status=row["outcome"], score=m.agg["score"] if m else None, cost=m.agg["tokens"] if m else None,
                 change=f"{self.idea.id} {row.get('change', '')}"[:300], artifact_id=art.id if art else None,
                 metrics={k: v for k, v in (m.agg.items() if m else [])},
                 meta={"idea": self.idea.id, "stage": row.get("stage"), "error": row.get("error")})
        return self.ledger.add(n)


# ------------------------------------------------------------------ composition
def _merge_json(base: dict, a: dict, b: dict) -> dict:
    out = dict(a)
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge_json(base.get(k, {}) if isinstance(base.get(k), dict) else {}, out[k], v)
        elif k not in base or base.get(k) != v:
            out[k] = v
    return out


def merge3(base: str, a: str, b: str) -> Optional[str]:
    """Line-level 3-way merge; None on conflicting overlapping edits."""
    if a == base:
        return b
    if b == base or a == b:
        return a
    bl, al, cl = base.splitlines(keepends=True), a.splitlines(keepends=True), b.splitlines(keepends=True)

    def edits(x):
        return [(i1, i2, x[j1:j2]) for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, bl, x).get_opcodes()
                if tag != "equal"]

    def overlap(s1, e1, s2, e2) -> bool:
        if s1 == e1 and s2 == e2:          # two insertions at the same point: ambiguous order
            return s1 == s2
        if s1 == e1:
            return s2 < s1 < e2
        if s2 == e2:
            return s1 < s2 < e1
        return max(s1, s2) < min(e1, e2)

    ea, eb = edits(al), edits(cl)
    for s1, e1, _ in ea:
        for s2, e2, _ in eb:
            if overlap(s1, e1, s2, e2):
                return None
    allx = sorted(ea + eb, key=lambda x: (x[0], x[1]))
    out, pos = [], 0
    for s, e, rep in allx:
        out.extend(bl[pos:s])
        out.extend(rep)
        pos = e
    out.extend(bl[pos:])
    return "".join(out)


def compose(base: Artifact, survivors: Sequence[Artifact]) -> tuple[Artifact, list[str]]:
    """Merge survivors' changes onto ``base``. Returns (composed, conflicts)."""
    files = dict(base.files)
    conflicts = []
    for k, cand in enumerate(survivors):
        for name in base.changed_files(cand):
            new = cand.get(name)
            cur = files.get(name)
            old = base.get(name)
            if name.endswith(".json") and cur is not None and new is not None and old is not None:
                try:
                    files[name] = json.dumps(_merge_json(json.loads(old), json.loads(cur), json.loads(new)),
                                             indent=1, sort_keys=True)
                    continue
                except json.JSONDecodeError:
                    pass
            if cur is None or old is None or new is None:
                if new is not None:
                    files[name] = new
                continue
            merged = merge3(old, cur, new)
            if merged is None:
                conflicts.append(f"survivor {k}: {name}")
            else:
                files[name] = merged
    return Artifact(files, meta={"composed_of": len(survivors)}), conflicts
