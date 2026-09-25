"""Leakage critic: reject edits that encode the evaluation data.

"Before anything is scored, an AI reviewer rejects edits that hard-code task names,
entity names, specific values or answers. Generic improvements still pass."

Two stages, as in RRSI ``rrsi/critic.py``:

1. a deterministic denylist scan of the *added* lines of the diff for leakage
   terms supplied by the domain (task ids, entities, literal answers) plus
   domain regexes;
2. an optional LLM review that judges whether the edit is suite-specific.

The critic sees the diff, never the scores. Methods can run a bounded repair
loop: feed ``objections`` back to the proposer and re-screen.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

from .llm import LLM, extract_json

CRITIC_SYSTEM = "You are a strict reviewer guarding an automated self-improvement loop against overfitting."

CRITIC_PROMPT = """\
An automated loop proposed the following edit to an AI system's artifact. The edit will be
scored on a fixed practice set of tasks, and we want to keep only edits that would also help
on UNSEEN tasks of the same kind.

REJECT the edit if it contains suite-specific logic, for example:
- hard-coded task names/ids, entity names, file names or literal values taken from practice tasks;
- lookup tables of answers, special cases keyed on particular inputs;
- instructions that only make sense for the specific practice tasks;
- attempts to detect or game the grader.
ACCEPT generic improvements: better general instructions, general tools, general control flow,
general verification, formatting fixes that apply to any task.

Domain description:
{domain}

Edit description: {change}

Unified diff:
```diff
{diff}
```

Reply with a JSON object: {{"verdict": "accept" | "reject", "objections": ["..."]}}
"""


@dataclass
class CriticVerdict:
    accept: bool
    objections: list[str] = field(default_factory=list)
    stage: str = "none"     # "denylist" | "llm" | "none"
    hits: list[str] = field(default_factory=list)


_HUNK = re.compile(r"^@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@")


def added_lines(diff: str) -> str:
    """Text of the lines a unified diff adds (without the leading ``+``).

    Hunk-aware: inside an ``@@`` hunk every ``+`` line is content, even one whose
    text itself starts with ``++`` (it would look like a ``+++`` file header).
    Outside hunks, ``+++`` headers are skipped and any other ``+`` line counts as
    added, so header-less or hand-written diffs are still screened."""
    out = []
    old_left = new_left = 0
    for line in (diff or "").splitlines():
        if old_left > 0 or new_left > 0:
            if line.startswith("+"):
                out.append(line[1:])
                new_left -= 1
                continue
            if line.startswith("-"):
                old_left -= 1
                continue
            if line.startswith(" ") or line == "":
                old_left -= 1
                new_left -= 1
                continue
            if line.startswith("\\"):   # "\ No newline at end of file"
                continue
            old_left = new_left = 0     # malformed counts: leave the hunk
        m = _HUNK.match(line)
        if m:
            old_left = int(m.group(1)) if m.group(1) is not None else 1
            new_left = int(m.group(2)) if m.group(2) is not None else 1
            continue
        if line.startswith("+") and not line.startswith("+++"):
            out.append(line[1:])
    return "\n".join(out)


class LeakageCritic:
    def __init__(
        self,
        terms: Iterable[str] = (),
        patterns: Sequence[str] = (),
        llm: Optional[LLM] = None,
        domain_brief: str = "",
        min_term_len: int = 4,
        case_sensitive: bool = False,
        parse_attempts: int = 3,
    ) -> None:
        self.terms = sorted({str(t) for t in terms if len(str(t)) >= min_term_len}, key=lambda t: (-len(t), t))
        self.patterns = [re.compile(p) for p in patterns]
        self.llm = llm
        self.domain_brief = domain_brief
        self.case_sensitive = case_sensitive
        self.parse_attempts = parse_attempts
        self.n_screened = 0
        self.n_rejected = 0

    def denylist_hits(self, diff: str) -> list[str]:
        text = added_lines(diff)
        hay = text if self.case_sensitive else text.lower()
        hits = []
        for t in self.terms:
            needle = str(t) if self.case_sensitive else str(t).lower()
            # whole-token match so a numeric answer "12" fires on neither "120" nor "3.12" nor "12.5"
            # (a sentence-ending "12." still matches)
            if re.search(r"(?<![\w.])" + re.escape(needle) + r"(?!\w)(?!\.\d)", hay):
                hits.append(str(t))
        for p in self.patterns:
            if p.search(text):
                hits.append(f"/{p.pattern}/")
        return hits

    def screen(self, diff: str, change: str = "") -> CriticVerdict:
        self.n_screened += 1
        hits = self.denylist_hits(diff)
        if hits:
            self.n_rejected += 1
            return CriticVerdict(False, [f"edit hard-codes evaluation data: {h!r}" for h in hits[:10]], "denylist", hits)
        if self.llm is None:
            return CriticVerdict(True, [], "none")
        prompt = CRITIC_PROMPT.format(domain=self.domain_brief or "(none)", change=change or "(none)",
                                      diff=diff[:30000])
        for attempt in range(self.parse_attempts):
            resp = self.llm.complete(prompt, system=CRITIC_SYSTEM, seed=attempt, role="critic")
            try:
                d = extract_json(resp.text)
                ok = str(d.get("verdict", "")).lower().startswith("acc")
                objections = d.get("objections") or []
                if isinstance(objections, str) or not isinstance(objections, (list, tuple)):
                    objections = [objections]
                if not ok:
                    self.n_rejected += 1
                return CriticVerdict(ok, [str(o) for o in objections], "llm")
            except (ValueError, AttributeError):
                continue
        # Unparseable reviews fail closed.
        self.n_rejected += 1
        return CriticVerdict(False, ["critic output unparseable"], "llm")
