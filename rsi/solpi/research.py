"""SoL-Pi research protocol: idea pool and oracle, map-reduce analysis, disposable lineages, composition."""
from __future__ import annotations

import difflib
import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Protocol, Sequence

import numpy as np

from ..core.artifact import Artifact
from ..core.domain import Trial
from ..core.evaluate import Evaluator
from ..core.ledger import Ledger, Node
from ..core.llm import Usage
from ..core.tasks import DECISION_SPLITS, SEALED_SPLITS
from .gate import DualGate, GateResult, Metrics, metrics_from_eval, nondominated

FAMILY_CODES = {"C": "Context", "P": "Progress", "T": "Tools", "D": "Delegation", "R": "Prompt & policy",
                "M": "Improvement & evaluation"}
SHARE_ORACLES = ("replayed_large_outputs", "diagnostic_log_tokens", "archivable_context", "tail_of_outputs",
                 "prompt_tokens", "verification_tokens", "late_turn_tokens")


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
        return sorted(self.ideas, key=lambda i: (-estimates.get(i.id, 0.0), i.id))[:n]

    def by_family(self) -> dict[str, int]:
        return dict(Counter(i.family for i in self.ideas))


def oracle_estimate(idea: Idea, trials: Sequence[Trial]) -> float:
    """Share of avoidable work the idea targets, from base trajectories (``Trial.meta['oracle']``)."""
    rows = [t.meta.get("oracle") for t in trials if isinstance(t.meta.get("oracle"), dict)]
    if not rows or idea.oracle == "none":
        return 0.05
    if idea.oracle == "adjacent_edit_command":
        return sum(r.get("adjacent_edit_command", 0) for r in rows) / (sum(r.get("transitions", 0) for r in rows) or 1)
    if idea.oracle in SHARE_ORACLES:
        return sum(r.get(idea.oracle, 0) for r in rows) / (sum(r.get("input_tokens", 0) for r in rows) or 1)
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


def heldout_only_families(domain) -> list[str]:
    """Family names that occur only in sealed splits (metadata only; no task content is read)."""
    suite = domain.tasks
    decision = {f for s in DECISION_SPLITS if s in suite.splits for f in suite.families(s)}
    sealed = {f for s in SEALED_SPLITS if s in suite.splits for f in suite.families(s)}
    return sorted(f for f in sealed - decision if f and f != "default")


class SmokeReviewer:
    """Contract review without an LLM: a clean smoke run and no held-out names in the added lines."""

    GENERIC_FORBIDDEN = ("holdout", "heldout", "ood/", "test.json")

    def __init__(self, domain, llm, forbidden: Optional[Sequence[str]] = None) -> None:
        if forbidden is None:
            forbidden = list(self.GENERIC_FORBIDDEN) + heldout_only_families(domain)
        self.domain, self.llm, self.forbidden = domain, llm, [f.lower() for f in forbidden if f]

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
    """Ralph loop: repair until the exit check passes; the returned ``usage`` totals every call."""
    errors = []
    total = prop.usage
    for _ in range(max_iters):
        if prop.artifact is None:
            errors.append(prop.error or "no artifact")
            if prop.meta.get("exhausted"):
                break
        else:
            err = exit_check(prop.artifact)
            if err is None:
                prop.usage = total
                return prop, errors
            errors.append(err)
        prop = proposer.fix(idea, prop, errors[-1])
        total = total + prop.usage
    prop.error = prop.error or f"exit check failed: {errors[-1] if errors else '?'}"
    prop.usage = total
    return prop, errors


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
    """One disposable loop for one idea on the training split: propose, implement, review, validate, gate."""

    def __init__(self, idea: Idea, *, evaluator: Evaluator, gate: DualGate, proposer: MechanismProposer,
                 reviewer: Reviewer, base: Artifact, base_metrics: Metrics, screen_split: str = "evolve",
                 rollout_tasks_per_family: int = 2, k: int = 1, max_iters: int = 4, ralph_max: int = 3,
                 workdir: Optional[Path] = None, ledger: Optional[Ledger] = None, sweep: bool = True,
                 tracer=None, review_max: int = 2) -> None:
        self.idea, self.ev, self.gate, self.proposer, self.reviewer = idea, evaluator, gate, proposer, reviewer
        self.base, self.base_metrics = base, base_metrics
        self.split, self.k, self.max_iters, self.ralph_max = screen_split, k, max_iters, ralph_max
        self.review_max = review_max    # review -> re-implementation rounds per iteration (0: a rejection ends it)
        self.rollout_n = rollout_tasks_per_family
        self.ledger = ledger
        self.sweep = sweep
        self.tracer = tracer            # write-only audit trace (SolpiTracer) or None
        fams = gate.spec.families
        self.screen = [t for t in evaluator.domain.tasks.split(screen_split) if fams is None or t.family in fams]

    def _rollout_tasks(self) -> list:
        out, per = [], Counter()
        for t in self.screen:
            if per[t.family] < self.rollout_n:
                out.append(t)
                per[t.family] += 1
        return out

    def _t(self, event: str, *args: Any, **kw: Any) -> Any:
        return getattr(self.tracer, event)(*args, **kw) if self.tracer is not None else None

    def run(self) -> LineageResult:
        res = self._run()
        self._t("lineage_end", self, res)
        return res

    def _run(self) -> LineageResult:
        idea, history, current, usage, parent, passing = self.idea, [], self.base, Usage(), None, []
        exit_check = lambda a: self.ev.domain.smoke(a, self.ev.llm)  # noqa: E731
        for it in range(self.max_iters):
            last = it + 1 >= self.max_iters
            self._t("lineage_iter_start", self, it, history)
            # 01 rollouts + 02 map-reduce analysis
            ro = self.ev.evaluate(current, self._rollout_tasks(), k=1, label="rollouts")
            evidence = reduce_findings([analyze(t) for trs in ro.trials.values() for t in trs])
            self._t("rollouts", current, ro, evidence)
            # 03 proposal (one mechanism) + 04 implementation (Ralph loop)
            prop = self.proposer.propose(idea, self.base, evidence, history)
            prop, ralph_errors = implement(self.proposer, idea, prop, exit_check, self.ralph_max)
            usage = usage + prop.usage
            name = self._t("proposal", self, it, prop, ralph_errors, self.base) or ""
            row: dict[str, Any] = {"iteration": it, "change": prop.change, "variant": prop.variant,
                                   "ralph_errors": ralph_errors}
            if prop.artifact is None or prop.error:
                row.update(stage="implementation", outcome="abandoned", error=prop.error)
                history.append(row)
                self._log(row, None, parent)
                stop = bool(prop.meta.get("exhausted"))
                self._t("decision", self, name, row, False, "lineage ends (proposer exhausted)" if stop else
                        "lineage ends (max_iters)" if last else "next iteration (01 new rollouts)")
                if stop:
                    break
                continue
            # 05 independent review; a rejection routes back to 04 with the objections, at most review_max times
            ok, why = self.reviewer.review(idea, self.base, prop.artifact)
            self._t("review", name, ok, why, self.reviewer)
            for n_rev in range(1, self.review_max + 1):
                if ok:
                    break
                fixed = self.proposer.fix(idea, prop, f"The independent reviewer rejected this implementation: {why}")
                fixed, errs = implement(self.proposer, idea, fixed, exit_check, self.ralph_max)
                usage = usage + fixed.usage
                row.setdefault("review_repairs", []).append({"objection": why[:500], "ralph_errors": errs,
                                                             "error": fixed.error})
                if fixed.artifact is None or fixed.error:
                    self._t("proposal", self, it, fixed, errs, self.base, repair=n_rev)
                    break
                prop = fixed
                row.update(change=prop.change, variant=prop.variant)
                name = self._t("proposal", self, it, prop, errs, self.base, repair=n_rev) or name
                ok, why = self.reviewer.review(idea, self.base, prop.artifact)
                self._t("review", name, ok, why, self.reviewer)
            if not ok:
                row.update(stage="review", outcome="rejected", error=why)
                history.append(row)
                self._log(row, prop.artifact, parent)
                self._t("decision", self, name, row, False,
                        "lineage ends (max_iters)" if last else "route back: next iteration")
                continue
            # 06 in-trajectory validation on the training screen + dual gate
            vr = self.ev.evaluate(prop.artifact, self.screen, k=self.k, label="screen")
            m = metrics_from_eval(vr)
            g = self.gate.accept(self.base_metrics, m)
            self._t("validation", name, vr, m, g)
            row.update(stage="validation", outcome="frozen" if g.accept else "gate_failed", gate=g.to_json(),
                       metrics=m.to_json())
            history.append(row)
            node = self._log(row, prop.artifact, parent, m)
            parent = node.id if node else parent
            if g.accept:
                fc = FrozenCandidate(f"{idea.id}:{prop.change}", idea, prop.artifact, m, g, evidence, it + 1)
                self._t("decision", self, name, row, True, "passing variant recorded; sweep continues" if self.sweep
                        else "freeze; lineage ends")
                if not self.sweep:
                    return LineageResult(idea, fc, history, usage)
                passing.append(fc)
            else:
                self._t("decision", self, name, row, False, "lineage ends (max_iters)" if last else
                        "route back to 01 with this candidate's rollouts")
            current = prop.artifact
        if not passing:
            return LineageResult(idea, None, history, usage)
        # freeze the nondominated passing variant with the best eta = cost / score
        eff = self.gate.spec.efficiency
        best = min(nondominated([(f, f.metrics) for f in passing], eff), key=lambda f: (
            f.metrics.agg.get("eta", float("inf")), f.metrics.agg[eff[0]], -f.metrics.agg["score"]))
        return LineageResult(idea, best, history, usage)

    def _log(self, row: dict, art: Optional[Artifact], parent: Optional[str], m: Optional[Metrics] = None):
        if self.ledger is None:
            return None
        n = Node(id=f"sp_{self.idea.id}_{row['iteration']}", parent=parent, round=row["iteration"], kind="lineage",
                 status=row["outcome"], score=m.agg["score"] if m else None, cost=m.agg["tokens"] if m else None,
                 change=f"{self.idea.id} {row.get('change', '')}"[:300], artifact_id=art.id if art else None,
                 metrics=dict(m.agg) if m else {},
                 meta={"idea": self.idea.id, "stage": row.get("stage"), "error": row.get("error")})
        return self.ledger.add(n)


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
    if any(overlap(s1, e1, s2, e2) for s1, e1, _ in ea for s2, e2, _ in eb):
        return None
    out, pos = [], 0
    for s, e, rep in sorted(ea + eb, key=lambda x: (x[0], x[1])):
        out.extend(bl[pos:s])
        out.extend(rep)
        pos = e
    out.extend(bl[pos:])
    return "".join(out)


def compose(base: Artifact, survivors: Sequence[Artifact]) -> tuple[Artifact, list[str]]:
    """Merge survivors' changes onto ``base`` (JSON union for ``.json``, else 3-way merge): (composed, conflicts)."""
    files = dict(base.files)
    conflicts = []
    for k, cand in enumerate(survivors):
        for name in base.changed_files(cand):
            new, cur, old = cand.get(name), files.get(name), base.get(name)
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
