"""Critic(H_t, H') -> {accept, reject}: the leakage screen (mirrors ``rrsi/critic.py``).

Reads the candidate diff BEFORE any evaluation is spent and rejects edits that encode
task names, entity names, task-specific values, answers or other suite-specific logic,
as well as degenerate, grader-gaming, undeclared-bundling, runtime-leaking or unbounded
edits. Two layers:

1. deterministic prechecks: :class:`rsi.core.LeakageCritic` denylist over the ADDED lines
   of the diff (the domain's leakage terms: evolve task ids, entities, literal answers)
   plus regexes (generic credential patterns + ``Domain.critic_patterns``);
2. an LLM review of intent and content (the code's six REJECT rules), up to 3 parse
   attempts; unparseable output fails closed.

A rejection is sent back to the proposer for a bounded number of repair rounds
(``Config.repair_rounds``) by :mod:`rsi.rrsi.loop`; a candidate that cannot be repaired
is dropped and recorded in the history without a measurement.
"""
from __future__ import annotations

import json
from typing import Optional, Sequence

from ..core.critic import LeakageCritic, added_lines
from ..core.llm import LLM, extract_json

GENERIC_PATTERNS: list[tuple[str, str]] = [
    (r"AIza[0-9A-Za-z_-]{35}|sk-[A-Za-z0-9]{20,}|api_key\s*=\s*[\"\'][^\"\']{8,}", "credential in diff"),
]

SYSTEM_TMPL = """You are a strict reviewer of harness (agent scaffold) code changes
in an evolution loop. The harness is evolved against the very tasks it is
measured on, so anti-overfitting review is critical. The change was made by
another model in response to aggregated failure modes. Your job is to catch
changes that would be cheating, degenerate, or self-destructive.

{domain_brief}

REJECT if ANY of:

1. LEAKAGE / TASK-SPECIALIZATION: the diff hard-codes knowledge of specific
   evolve-set tasks: task names or ids, entity/company/case names, task-specific
   file names, expected outputs or answers, magic constants that only make
   sense for one task, per-task templates, a list of the suite's domains, or
   branching on task-identifying features ("if the task mentions X"). General
   professional practice and general procedure are fine. Litmus test: would
   this change still make sense, and still help, on an unfamiliar task from a
   different suite in the same kind of work?
2. DEGENERATE: the diff is effectively a no-op (dead code, unused flag,
   comment-only) while claiming a mechanism, or it deletes/disables an
   existing safety mechanism (context compression, output truncation, error
   handling, termination guards) without a working replacement.
3. GRADER GAMING: the change tries to read, detect, reconstruct or influence
   the verifier / judge / rubric at runtime, hard-codes a threshold or reference
   value that could only have come from a grader, or games the completion or
   output format for the grader rather than improving actual task performance.
4. UNDECLARED BUNDLING: multiple DECLARED edits per candidate are allowed and
   normal (the declared edit list is in the payload). Reject only if the diff
   contains independent changes not covered by ANY declared edit, or one
   declared edit secretly bundles several independent mechanisms.
5. RUNTIME MEMORY / SKILL LEAKAGE: if an edit adds a memory/state or skill
   mechanism, inspect what it PERSISTS or INJECTS at runtime. Entity-free
   general procedures are fine. REJECT if the code path can persist or inject
   task-specific runtime data across trials (captured file contents, tool
   outputs, computed answers, task/entity/file names, per-task solutions keyed
   by task identity): the harness is evaluated on the tasks it evolves on, so
   that is memorization, not a general improvement.
6. UNBOUNDED WORK: an added check, retry or "keep improving" loop with no
   give-up path, or anything that implies finishing should wait for a
   confirmation that may never come. A bounded pass with an explicit exit is
   fine.

Otherwise ACCEPT. You are reviewing intent and content, not style.
Runtime correctness (undefined names, crashes, syntax) is NOT your job:
deterministic compile/constructor/smoke checks handle that after you, and you
only see the diff (not the full files), so never speculate that something is
undefined or would crash.
Return STRICT JSON:
{{"verdict": "accept" | "reject", "reasons": ["..."], "risk_notes": ["..."]}}"""

DIFF_CAP = 120_000
EDITS_CAP = 20_000


class RRSICritic:
    """Regex/denylist precheck (core :class:`LeakageCritic`) followed by an LLM review.

    Parameters
    ----------
    domain:
        supplies ``leakage_terms(split)``, ``describe()`` and optional ``critic_patterns``
        (list of ``(regex, why)``) and ``critic_brief``.
    llm:
        reviewer backend (role ``"critic"``); ``None`` = deterministic precheck only.
    terms, patterns:
        extra denylist terms / ``(regex, why)`` patterns.
    """

    def __init__(self, domain=None, llm: Optional[LLM] = None, *, terms: Optional[Sequence[str]] = None,
                 patterns: Sequence[tuple[str, str]] = (), domain_brief: Optional[str] = None,
                 leakage_split: str = "evolve", parse_attempts: int = 3, min_term_len: int = 4) -> None:
        self.llm = llm
        dom_terms = list(domain.leakage_terms(leakage_split)) if (domain is not None and terms is None) else []
        dom_pats = list(getattr(domain, "critic_patterns", []) or []) if domain is not None else []
        self.patterns = list(GENERIC_PATTERNS) + dom_pats + list(patterns)
        self._why = {p: why for p, why in self.patterns}
        self.denylist = LeakageCritic(terms=list(terms or []) + dom_terms, patterns=[p for p, _ in self.patterns],
                                      min_term_len=min_term_len)
        brief = domain_brief
        if brief is None and domain is not None:
            brief = getattr(domain, "critic_brief", None) or domain.describe()
        self.domain_brief = brief or ""
        self.parse_attempts = parse_attempts
        self.n_reviews = 0
        self.n_precheck_rejects = 0
        self.n_llm_rejects = 0

    def precheck(self, diff: str) -> list[str]:
        hits = []
        for h in self.denylist.denylist_hits(diff):
            if h.startswith("/") and h.endswith("/") and h[1:-1] in self._why:
                hits.append(self._why[h[1:-1]])
            else:
                hits.append(f"hard-coded evaluation data {h!r}")
        return hits

    def review(self, diff: str, summary: str = "", targets_mode: str = "", edits: Optional[list] = None,
               state_files: str = "", seed: int = 0) -> dict:
        """Return ``{"verdict": "accept"|"reject", "reasons": [...], "risk_notes": [...], "stage": ...}``."""
        self.n_reviews += 1
        hard = self.precheck(diff)
        if hard:
            self.n_precheck_rejects += 1
            return {"verdict": "reject", "reasons": [f"precheck: {h}" for h in hard[:10]], "risk_notes": [],
                    "stage": "precheck"}
        if not (diff or "").strip():
            return {"verdict": "reject", "reasons": ["empty diff"], "risk_notes": [], "stage": "precheck"}
        if self.llm is None:
            return {"verdict": "accept", "reasons": [], "risk_notes": ["no LLM reviewer configured"],
                    "stage": "precheck"}
        system = SYSTEM_TMPL.format(domain_brief=self.domain_brief)
        payload = (f"CANDIDATE SUMMARY: {summary}\nTARGETS: {targets_mode}\n\n"
                   f"=== DECLARED EDITS (independent changes in this candidate) ===\n"
                   f"{json.dumps(edits or [], ensure_ascii=False, indent=1, default=str)[:EDITS_CAP]}\n\n"
                   f"=== DIFF ===\n{diff[:DIFF_CAP]}\n\n"
                   f"=== STATE FILES (if a mechanism persists state) ===\n{state_files[:20_000]}")
        last = ""
        for attempt in range(self.parse_attempts):
            resp = self.llm.complete(payload, system=system, seed=seed * 10 + attempt, role="critic")
            last = resp.text
            if not resp.ok:
                continue
            try:
                v = extract_json(resp.text)
            except ValueError:
                continue
            if isinstance(v, dict) and str(v.get("verdict", "")).lower() in ("accept", "reject"):
                v["verdict"] = str(v["verdict"]).lower()
                v.setdefault("reasons", [])
                v.setdefault("risk_notes", [])
                v["stage"] = "llm"
                if v["verdict"] == "reject":
                    self.n_llm_rejects += 1
                return v
        self.n_llm_rejects += 1
        return {"verdict": "reject", "reasons": [f"critic output unparseable after {self.parse_attempts} attempts: "
                                                 f"{last[:200]}"], "risk_notes": [], "stage": "llm"}


def added_text(diff: str) -> str:
    """Added lines of a diff (what the denylist scans)."""
    return added_lines(diff)


def leak_hits(diff: str, terms: Sequence[str]) -> list[str]:
    """Convenience: denylist hits of ``terms`` in the added lines of ``diff``."""
    return LeakageCritic(terms=terms).denylist_hits(diff)

