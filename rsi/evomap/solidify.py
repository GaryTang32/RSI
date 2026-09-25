"""Solidify: the keep rule (spec §3.6, §4.8, §4.10, §4.12, §4.13).

After a cycle's change, :class:`Solidifier` measures blast radius from the
workspace diff (counted-file policy), checks constraints, runs the gene's
validation, computes the composite process score, classifies the failure mode,
writes the EvolutionEvent (+ Capsule on success, FailedCapsule on failure with a
non-empty diff), updates the gene's learning history / anti-patterns /
epigenetic marks, and rolls back on failure.

``mode="faithful"`` - success = constraints ok AND validation ok (with the
faithful runner: empty / skipped = ok) AND no protocol violations.
``mode="safe"`` - additionally the validation must be non-vacuous (lint +
discriminative, :class:`~rsi.evomap.validation.VacuityDetector`) and, when
``require_task_success``, the cycle's graded task result on the agent's OWN
decision task must be a success (``RunState.task_success``).
"""
from __future__ import annotations

import difflib
import fnmatch
import re
from dataclasses import dataclass, field
from typing import Mapping, Optional, Sequence

from .assets import Capsule, EvolutionEvent, Gene, Mutation, PersonalityState
from .validation import ValidationResult, ValidationRunner, VacuityDetector, VacuityVerdict

HARD_CAP_FILES = 60
HARD_CAP_LINES = 20000
BROADCAST_SCORE = 0.7
BROADCAST_STREAK = 2
MIN_PUBLISH_SCORE = 0.78
A2A_MAX_FILES, A2A_MAX_LINES = 5, 200
CONTENT_MAX = 8000

ETHICS = [
    (r"(bypass|disable|turn off)\s+(the\s+)?(safety|guard|security|tests?|validation)", "ethics:bypass_safety"),
    (r"covert(ly)?\s+monitor|keylog|exfiltrat", "ethics:covert_monitoring"),
    (r"phish|social[- ]engineering template|impersonat", "ethics:social_engineering"),
    (r"exploit (the )?users?", "ethics:exploit_users"),
    (r"(hide|conceal)\s+(this|actions?|changes?)\s+from\s+(the\s+)?(audit|log|user)", "ethics:conceal_from_audit"),
]


@dataclass
class CountedFilePolicy:
    """What counts toward blast radius. Domain-configurable (Evolver's default
    counts only JS/config files; a Python port must count ``.py``)."""

    include_ext: tuple = (".py", ".js", ".ts", ".json", ".yaml", ".yml", ".toml", ".ini", ".sh", ".md", ".txt")
    exclude_prefixes: tuple = ("logs/", "memory/", ".evolver/", "assets/gep/", "out/", "temp/", "__pycache__/")
    exclude_patterns: tuple = ("*capsule*", "*events.jsonl", "*event.jsonl", "*.pyc")

    def counts(self, path: str) -> bool:
        if path.startswith(self.exclude_prefixes):
            return False
        if any(fnmatch.fnmatch(path, p) for p in self.exclude_patterns):
            return False
        return path.endswith(self.include_ext)


def diff_text(before: Mapping[str, str], after: Mapping[str, str], max_chars: int = CONTENT_MAX) -> str:
    chunks = []
    for name in sorted(set(before) | set(after)):
        a, b = before.get(name), after.get(name)
        if a == b:
            continue
        chunks.extend(difflib.unified_diff((a or "").splitlines(keepends=True), (b or "").splitlines(keepends=True),
                                           f"a/{name}", f"b/{name}", n=1))
    return "".join(chunks)[:max_chars]


def blast_radius(before: Mapping[str, str], after: Mapping[str, str],
                 policy: Optional[CountedFilePolicy] = None) -> dict:
    """{files, lines, changed, counted}: lines = added + deleted on counted files
    (new files count all their lines)."""
    policy = policy or CountedFilePolicy()
    changed = sorted(n for n in set(before) | set(after) if before.get(n) != after.get(n))
    counted = [n for n in changed if policy.counts(n)]
    lines = 0
    for n in counted:
        a, b = (before.get(n) or "").splitlines(), (after.get(n) or "").splitlines()
        for ln in difflib.unified_diff(a, b, n=0, lineterm=""):
            if (ln.startswith("+") or ln.startswith("-")) and not ln.startswith(("+++", "---")):
                lines += 1
    return {"files": len(counted), "lines": lines, "changed": changed, "counted": counted}


@dataclass
class ConstraintResult:
    ok: bool
    violations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    blast: dict = field(default_factory=dict)


class ConstraintChecker:
    def __init__(self, *, protected_paths: Sequence[str] = (), critical_files: Sequence[str] = (),
                 default_max_files: int = 20) -> None:
        self.protected_paths = tuple(protected_paths)
        self.critical_files = tuple(critical_files)
        self.default_max_files = default_max_files

    def check(self, gene: Optional[Gene], blast: dict, estimate: Optional[dict] = None,
              before: Optional[Mapping[str, str]] = None, after: Optional[Mapping[str, str]] = None) -> ConstraintResult:
        v, w = [], []
        files, lines = blast.get("files", 0), blast.get("lines", 0)
        if files > HARD_CAP_FILES or lines > HARD_CAP_LINES:
            v.append(f"HARD CAP BREACH: {files} files / {lines} lines")
        mf = gene.max_files if gene is not None else self.default_max_files
        if files > 2 * mf:
            v.append(f"critical_overrun: {files} > 2*max_files={2 * mf}")
        elif files > mf:
            v.append(f"max_files exceeded: {files} > {mf}")
        elif files > 0.8 * mf:
            w.append(f"approaching_limit: {files} > 0.8*{mf}")
        if estimate and estimate.get("files"):
            ratio = files / max(1, estimate["files"])
            if ratio > 3:
                w.append(f"estimate_drift: actual/estimate={ratio:.1f}")
        forb = gene.forbidden_paths if gene is not None else []
        for path in blast.get("changed", []):
            if any(path == f or path.startswith(f.rstrip("/") + "/") for f in forb):
                v.append(f"forbidden_path touched: {path}")
            if any(path == p or path.startswith(p.rstrip("/") + "/") for p in self.protected_paths):
                v.append(f"critical_path_modified: {path}")
        if blast.get("changed") and not blast.get("counted"):
            v.append("hollow_commit: files changed but none counted")
        if gene is not None:
            text = (gene.summary + "\n" + "\n".join(gene.strategy)).lower()
            for rx, tag in ETHICS:
                if re.search(rx, text):
                    v.append(tag)
        if before is not None and after is not None:
            for cf in self.critical_files:
                if cf in before and (cf not in after):
                    v.append(f"CRITICAL_FILE_DELETED: {cf}")
                elif cf in before and not (after.get(cf) or "").strip():
                    v.append(f"CRITICAL_FILE_EMPTIED: {cf}")
        return ConstraintResult(not v, v, w, blast)


def composite_score(*, n_signals: int, gene: Optional[Gene], mutation: Optional[Mutation], blast: dict,
                    max_files: Optional[int], estimate: Optional[dict], n_violations: int, validation: ValidationResult,
                    n_protocol: int, canary_failed: bool = False, hollow: bool = False) -> float:
    """§4.10 weights: .05 signal + .10 selection + .05 mutation + .15 blast + .25 constraint
    + .25 validation + .10 protocol + .05 canary."""
    sig = 0.5 if n_signals == 0 else min(1.0, 0.4 + 0.1 * n_signals)
    sel = 0.3 if gene is None else (0.7 if gene.id.startswith("gene_auto_") else 0.9)
    if mutation is None:
        mut = 0.3
    elif mutation.rationale and mutation.category:
        mut = 0.9 if mutation.risk_level == "low" else (0.6 if mutation.risk_level == "high" else 0.8)
    else:
        mut = 0.5
    mf = max_files or 12
    files = blast.get("files")
    if files is None:
        bc = 0.5
    elif hollow:
        bc = 0.0
    elif files == 0:
        bc = 0.4
    elif files <= 0.5 * mf:
        bc = 1.0
    elif files <= mf:
        bc = 0.7
    else:
        bc = 0.2
    if estimate and estimate.get("files") and files:
        r = files / estimate["files"]
        bc *= 0.5 if r > 3 else (0.7 if r > 2 else 1.0)
    cons = max(0.0, 1 - 0.25 * n_violations)
    val = validation.component
    prot = max(0.0, 1 - 0.3 * n_protocol)
    can = 0.0 if canary_failed else 1.0
    s = .05 * sig + .10 * sel + .05 * mut + .15 * bc + .25 * cons + .25 * val + .10 * prot + .05 * can
    return max(0.0, min(1.0, round(s, 2)))


HARD_RX = re.compile(r"HARD CAP BREACH|CRITICAL_FILE_|critical_path_modified|forbidden_path touched|ethics:")


def classify_failure_mode(violations: Sequence[str], protocol: Sequence[str], validation_ok: bool,
                          canary_failed: bool = False) -> tuple[str, str, bool]:
    """(mode, reason_class, retryable) - hard: constraint/protocol/canary; soft: validation."""
    if any(HARD_RX.search(v) for v in violations):
        return "hard", "constraint_destructive", False
    if protocol:
        return "hard", "protocol", False
    if canary_failed:
        return "hard", "canary", False
    if violations:
        return "hard", "constraint", False
    if not validation_ok:
        return "soft", "validation", True
    return "soft", "unknown", True


def apply_epigenetic_marks(gene: Gene, env: str, success: bool, now_s: float, created_at: str = "") -> None:
    marks = [m for m in (gene.epigenetic_marks or []) if isinstance(m, dict)]
    marks = [m for m in marks if now_s - float(m.get("t", now_s)) <= 90 * 86400]
    for m in marks:
        if m.get("context") == env:
            m["boost"] = round(min(0.5, m["boost"] + 0.05) if success else max(-0.5, m["boost"] - 0.1), 4)
            m["reason"] = "success_in_environment" if success else "failure_in_environment"
            break
    else:
        marks.append({"context": env, "boost": 0.1 if success else -0.1,
                      "reason": "success_in_environment" if success else "failure_in_environment",
                      "created_at": created_at, "t": now_s})
    gene.epigenetic_marks = marks[-10:]


# ----------------------------------------------------------------------------- solidify
@dataclass
class RunState:
    """Engine-held state of one cycle (Evolver's ``last_run``)."""

    run_id: str
    signals: list[str]
    gene: Optional[Gene]
    mutation: Optional[Mutation]
    personality: PersonalityState
    before: dict
    after: dict
    parent_event_id: Optional[str] = None
    capsule: Optional[Capsule] = None
    estimate: Optional[dict] = None
    source_type: str = "generated"
    reused_asset_id: Optional[str] = None
    personality_known: bool = False
    intent: Optional[str] = None
    summary: str = ""
    task_id: str = ""
    task_success: Optional[bool] = None     # graded on the agent's own decision task (safe mode may use it)
    hidden_score: Optional[float] = None    # measurement only; never read by the keep rule
    derivation_tokens: Optional[dict] = None
    env: str = "sim/py3.11"
    validation_context: object = None


@dataclass
class SolidifyResult:
    success: bool
    score: float
    event: EvolutionEvent
    capsule: Optional[Capsule]
    validation: ValidationResult
    constraints: ConstraintResult
    protocol_violations: list[str]
    failure_mode: Optional[tuple] = None
    failed_capsule: Optional[dict] = None
    rolled_back: bool = False
    vacuity: Optional[VacuityVerdict] = None
    eligible_to_broadcast: bool = False
    publishable: bool = False


class Solidifier:
    def __init__(self, store, runner: ValidationRunner, *, mode: str = "faithful",
                 constraints: Optional[ConstraintChecker] = None, counted: Optional[CountedFilePolicy] = None,
                 vacuity: Optional[VacuityDetector] = None, require_task_success: bool = True,
                 rollback: str = "stash") -> None:
        self.store = store
        self.runner = runner
        self.mode = mode
        self.constraints = constraints or ConstraintChecker()
        self.counted = counted or CountedFilePolicy()
        self.vacuity = vacuity
        self.require_task_success = require_task_success
        self.rollback = rollback

    def solidify(self, rs: RunState, *, dry_run: bool = False) -> SolidifyResult:
        clock = self.store.clock
        gene, mut, pers = rs.gene, rs.mutation, rs.personality
        pv: list[str] = []
        if mut is None or not mut.category:
            pv.append("missing_or_invalid_mutation")
        if pers is None:
            pv.append("missing_or_invalid_personality_state")
        if mut is not None and mut.risk_level == "high":
            if not (pers.rigor >= 0.6 and pers.risk_tolerance <= 0.5):
                pv.append("high_risk_mutation_not_allowed_by_personality")
            if not rs.personality_known:
                pv.append("high_risk_mutation_forbidden_under_unknown_personality")
        if mut is not None and mut.category == "innovate" and (pers.rigor < 0.5 or pers.risk_tolerance > 0.6):
            pv.append("forbidden_innovate_with_high_risk_personality")
        if rs.intent and mut is not None and rs.intent != mut.category:
            pv.append(f"intent_mismatch_with_mutation:{rs.intent}!={mut.category}")
        blast = blast_radius(rs.before, rs.after, self.counted)
        cc = self.constraints.check(gene, blast, rs.estimate, rs.before, rs.after)
        specs = list(gene.validation) if gene is not None else []
        if gene is None and self.mode == "faithful":
            val = ValidationResult(True, self.runner.run([], rs.after, gene_id="none").report)
        else:
            val = self.runner.run(specs, rs.after, gene_id=gene.id if gene else "", context=rs.validation_context)
        vac = None
        extra_fail: list[str] = []
        if self.mode == "safe":
            if self.vacuity is not None:
                vac = self.vacuity.verdict(specs, rs.before, rs.after, context=rs.validation_context)
                if vac.vacuous:
                    extra_fail.append("vacuous_validation:" + ",".join(vac.reasons[:3]))
            if self.require_task_success and rs.task_success is False:
                extra_fail.append("task_check_failed")
        success = cc.ok and val.ok and not pv and not extra_fail
        hollow = any(v.startswith("hollow_commit") for v in cc.violations)
        score = composite_score(n_signals=len(rs.signals), gene=gene, mutation=mut, blast=blast,
                                max_files=gene.max_files if gene else 12, estimate=rs.estimate,
                                n_violations=len(cc.violations), validation=val, n_protocol=len(pv), hollow=hollow)
        fm = None if success else classify_failure_mode(cc.violations + extra_fail, pv, val.ok)
        st = self.store
        vr = val.report
        if vac is not None:
            vr.discriminative = vac.to_dict()
            vr.stamp()
        ev_id = st.new_id("evt")
        capsule = None
        if success:
            cap_id = rs.capsule.id if rs.capsule is not None else st.new_id("capsule")
            trace = [{"step": 1, "stage": "build", "cmd": f"apply -- {blast['files']} file(s), {blast['lines']} line(s)",
                      "exit": 0}]
            for i, row in enumerate(vr.commands):
                if row.get("skipped"):
                    continue
                trace.append({"step": len(trace) + 1, "stage": "validate", "cmd": row.get("command", ""),
                              "exit": 0 if row.get("ok") else 1, "stdout_tail": str(row.get("stdout", ""))[-300:]})
            capsule = Capsule(id=cap_id, gene=gene.id if gene else "none",
                              trigger=list(rs.capsule.trigger) if rs.capsule is not None else list(rs.signals[:8]),
                              summary=rs.summary or f"{mut.category if mut else 'repair'} via "
                                                    f"{gene.id if gene else 'no gene'} on {rs.task_id}",
                              confidence=score, blast_radius={"files": blast["files"], "lines": blast["lines"]},
                              outcome={"status": "success", "score": score}, success_reason="validation passed",
                              source_type=rs.source_type, reused_asset_id=rs.reused_asset_id,
                              content=("\n".join(gene.strategy) if gene else rs.summary)[:CONTENT_MAX],
                              diff=diff_text(rs.before, rs.after), strategy=list(gene.strategy) if gene else [],
                              execution_trace=trace, derivation_tokens=rs.derivation_tokens,
                              env_fingerprint={"context": rs.env},
                              gene_library_version=st.gene_library_version())
        event = EvolutionEvent(id=ev_id, parent=rs.parent_event_id if rs.parent_event_id is not None else st.last_event_id(),
                               intent=mut.category if mut else "repair", signals=list(rs.signals),
                               genes_used=[gene.id] if gene else [], mutation_id=mut.id if mut else "",
                               personality_state=pers.to_dict() if pers else None,
                               blast_radius={"files": blast["files"], "lines": blast["lines"]},
                               outcome={"status": "success" if success else "failed", "score": score},
                               capsule_id=capsule.id if capsule else None, source_type=rs.source_type,
                               reused_asset_id=rs.reused_asset_id, validation_report_id=vr.id,
                               env_fingerprint={"context": rs.env},
                               meta={"run_id": rs.run_id, "task_id": rs.task_id, "violations": cc.violations,
                                     "protocol_violations": pv, "extra_failures": extra_fail,
                                     "validation_ok": val.ok, "n_validation_run": val.n_run,
                                     "n_validation_skipped": val.n_skipped, "hidden_score": rs.hidden_score,
                                     "failure_mode": list(fm) if fm else None, "solidify_mode": self.mode,
                                     "gene_library_version": st.gene_library_version()})
        res = SolidifyResult(success, score, event, capsule, val, cc, pv, fm, vacuity=vac)
        if dry_run:
            return res
        if not success and blast["changed"]:
            res.failed_capsule = {"id": st.new_id("failed_capsule"), "gene": gene.id if gene else None,
                                  "trigger": list(rs.signals[:8]), "diff_snapshot": diff_text(rs.before, rs.after),
                                  "failure_reason": (cc.violations + pv + extra_fail +
                                                     ([] if val.ok else ["validation_failed"]))[:6],
                                  "learning_signals": list(rs.signals[:8]), "constraint_violations": cc.violations}
            st.append_failed_capsule(res.failed_capsule)
        if not success and self.rollback != "none":
            res.rolled_back = True
        if gene is not None:
            learn = [s for s in rs.signals if s.startswith(("problem:", "area:"))] or list(rs.signals)
            entry = {"at": clock.iso(), "outcome": "success" if success else "failed",
                     "mode": fm[0] if fm else "none", "reason_class": fm[1] if fm else "ok",
                     "retryable": fm[2] if fm else True, "learning_signals": learn[:12]}
            gene.learning_history = (gene.learning_history + [entry])[-20:]
            if success:
                for s in rs.signals:
                    if s.startswith(("problem:", "area:")) and s not in gene.signals_match:
                        gene.signals_match.append(s)
            else:
                gene.anti_patterns = (gene.anti_patterns + [{"at": clock.iso(), "mode": fm[0], "reason_class": fm[1],
                                                             "learning_signals": learn[:8]}])[-12:]
            apply_epigenetic_marks(gene, rs.env, success, clock.now(), clock.iso())
            if st.has_gene(gene.id):
                st.upsert_gene(gene)
        st.append_report(vr)
        st.append_event(event)
        if capsule is not None:
            capsule.success_streak = st.success_streak(capsule.id)
            safe_blast = blast["files"] <= A2A_MAX_FILES and blast["lines"] <= A2A_MAX_LINES
            res.eligible_to_broadcast = safe_blast and score >= BROADCAST_SCORE and capsule.success_streak >= \
                BROADCAST_STREAK
            capsule.a2a = {"eligible_to_broadcast": res.eligible_to_broadcast}
            res.publishable = res.eligible_to_broadcast and score >= MIN_PUBLISH_SCORE and rs.source_type != "reused"
            st.upsert_capsule(capsule)
        return res
