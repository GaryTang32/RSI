"""The editable component set K, its structural subset K_str, and diff-based tagging
(mirrors ``rrsi/components.py``).

The paper tags every candidate edit with the harness component l' it modifies. K is
the fixed vocabulary of those tags; K_str are the components that add machinery (a
tool, a skill file, a memory store, an extra policy call) rather than changing text
or constants. A proposer *declares* the component of each edit; the declaration is
kept only if the diff carries evidence for it, otherwise the tag is recovered from
the diff, so a mislabelled prompt tweak cannot pass as a skill (and cannot fake a
reserved exploration slot or the novelty bonus).

Domain-agnostic version: K and K_str come from ``Domain.components`` /
``Domain.structural_components`` when the domain defines them (else the paper's K).
Each ``Domain.components[c]`` entry is a list of *path globs* matched against the
files a diff touches (``"prompts/*"``), or regexes prefixed with ``re:`` matched
against the diff text. An optional ``Domain.component_signals`` list of
``(component, [regex, ...])`` pairs (the released code's format) is honoured too,
and the paper's generic structural signals (``Memory(``, ``skills/``,
``ToolRegistry``, ``subcall(`` ...) apply last.

A domain that declares NO taxonomy at all (no ``components``, no ``component_signals``;
e.g. a plain :class:`rsi.core.FunctionDomain`) gets ``trust_code_tags``: with no signal
for the non-structural components, the released code's rule would re-tag every code edit
as ``prompt``, so T_t collapses to {prompt} and every reserved exploration slot is
rejected. In that mode a declared NON-structural tag is kept for a code (non-text-only)
diff, and an unverifiable code edit defaults to ``control_flow``; structural tags still
need the generic structural evidence, so novelty and reserved slots on K_str cannot be
faked. Declare ``Domain.components`` to get the faithful evidence rule.
"""
from __future__ import annotations

import fnmatch
import re
from typing import Iterable, Optional, Sequence

K = ["prompt", "control_flow", "config", "output_plumbing", "context_mgmt",
     "client_tool", "skill", "memory", "subagent"]
K_STR = ["client_tool", "skill", "memory", "subagent"]

#: Signals shared by every domain (structural substrate names in the released mechanisms.py).
GENERIC_SIGNALS: list[tuple[str, list[str]]] = [
    ("memory", [r"\bMemory\(", r"\.remember\(", r"\.recall\(", r"_STATE_DIR"]),
    ("skill", [r"skills/", r"SkillRegistry", r"skill_use", r"skill_catalog", r"upload_skills"]),
    ("client_tool", [r"ToolRegistry", r"register_tool", r"tool_spec", r"CLIENT_TOOLS"]),
    ("subagent", [r"\bsubcall\(", r"sub_agent", r"subagent"]),
]

#: Tag aliases between common vocabularies (e.g. AgentQA calls client tools "tool").
ALIASES = {"client_tool": "tool", "tool": "client_tool", "tools": "tool", "prompts": "prompt",
           "control": "control_flow", "skills": "skill", "subagents": "subagent", "agents": "subagent"}

_STRING_LINE = re.compile(r'^[+-]\s*(?:[frb]?["\']|""")')
_PATH_LINE = re.compile(r"^(?:\+\+\+|---) (?:[ab]/)?(\S+)", re.M)


def changed_lines(diff: str) -> list[str]:
    return [l for l in (diff or "").splitlines()
            if (l.startswith("+") or l.startswith("-")) and not l.startswith("+++") and not l.startswith("---")
            and l[1:].strip()]


def text_only(diff: str) -> bool:
    """True when every changed line is a string literal or a comment: a model-facing
    text edit, i.e. ``prompt``, whatever words the prose contains."""
    ch = changed_lines(diff)
    if not ch:
        return False
    return all(_STRING_LINE.match(l) or l[1:].lstrip().startswith("#") for l in ch)


def changed_paths(diff: str) -> list[str]:
    """Files named in the unified-diff headers (``--- a/x`` / ``+++ b/x``)."""
    out = []
    for p in _PATH_LINE.findall(diff or ""):
        if p != "/dev/null" and p not in out:
            out.append(p)
    return out


class Taxonomy:
    """K, K_str and the evidence signals of one domain."""

    def __init__(self, components: Sequence[str] = tuple(K), structural: Sequence[str] = tuple(K_STR),
                 path_hints: Optional[dict[str, list[str]]] = None,
                 signals: Optional[list[tuple[str, list[str]]]] = None, *, trust_code_tags: bool = False) -> None:
        self.K = list(components)
        self.K_str = [c for c in structural if c in self.K]
        self.path_hints = {c: list(v) for c, v in (path_hints or {}).items() if c in self.K}
        self.signals = [(c, list(p)) for c, p in (signals or []) if c in self.K]
        self.trust_code_tags = trust_code_tags
        # evaluation order: explicit domain regexes, then path hints (structural first, prompt last),
        # then the generic structural signals.
        order = self.K_str + [c for c in self.K if c not in self.K_str and c != "prompt"] + \
            (["prompt"] if "prompt" in self.K else [])
        self._path_order = [c for c in order if c in self.path_hints]

    @classmethod
    def from_domain(cls, domain) -> "Taxonomy":
        comps = dict(getattr(domain, "components", None) or {})
        if not comps:
            sig = list(getattr(domain, "component_signals", []) or [])
            return cls(K, K_STR, {}, sig, trust_code_tags=not sig)
        structural = list(getattr(domain, "structural_components", ()) or [c for c in K_STR if c in comps])
        paths: dict[str, list[str]] = {}
        regs: list[tuple[str, list[str]]] = list(getattr(domain, "component_signals", []) or [])
        for c, hints in comps.items():
            globs = [h for h in hints if not h.startswith("re:")]
            rx = [h[3:] for h in hints if h.startswith("re:")]
            if globs:
                paths[c] = globs
            if rx:
                regs.append((c, rx))
        return cls(list(comps), structural, paths, regs)

    # ------------------------------------------------------------------ evidence --
    def _generic(self) -> list[tuple[str, list[str]]]:
        out = []
        for c, pats in GENERIC_SIGNALS:
            cc = c if c in self.K else ALIASES.get(c)
            if cc in self.K:
                out.append((cc, pats))
        return out

    def _path_match(self, component: str, paths: Iterable[str]) -> bool:
        globs = self.path_hints.get(component, [])
        return any(fnmatch.fnmatch(p, g) or fnmatch.fnmatch(p, g.rstrip("*") + "*") for p in paths for g in globs)

    def has_evidence(self, component: str, diff: str) -> bool:
        """Does the diff contain any signal of ``component``?"""
        paths = changed_paths(diff)
        if self._path_match(component, paths):
            return True
        for comp, pats in self.signals + self._generic():
            if comp == component and any(re.search(p, diff) for p in pats):
                return True
        if self.trust_code_tags and component in self.K and component not in self.K_str:
            # taxonomy-less domain: nothing can refute a non-structural tag; keep it for a code change
            return component == "prompt" or (bool(changed_lines(diff)) and not text_only(diff))
        return False

    def classify(self, diff: str) -> str:
        """First matching component (domain signals, domain paths, generic); default ``prompt``
        (``control_flow`` for a code change on a taxonomy-less domain)."""
        default = "prompt" if "prompt" in self.K else self.K[0]
        if text_only(diff) and "prompt" in self.K:
            return "prompt"
        if self.trust_code_tags and "control_flow" in self.K and changed_lines(diff):
            default = "control_flow"
        for comp, pats in self.signals:
            if any(re.search(p, diff) for p in pats):
                return comp
        paths = changed_paths(diff)
        for comp in self._path_order:
            if self._path_match(comp, paths):
                return comp
        for comp, pats in self._generic():
            if any(re.search(p, diff) for p in pats):
                return comp
        return default

    def canonical(self, declared: Optional[str]) -> str:
        d = (declared or "").strip().lower()
        if d not in self.K and ALIASES.get(d) in self.K:
            d = ALIASES[d]
        return d

    def normalize(self, declared: Optional[str], diff: str) -> str:
        """Keep a declared tag only if it is in K and evidenced by the diff; else re-tag
        from the diff. As in the code, callers pass the WHOLE candidate diff."""
        d = self.canonical(declared)
        if d in self.K and self.has_evidence(d, diff):
            return d
        return self.classify(diff)

    def novelty(self, edit_components: Iterable[str], incumbent_counts: dict) -> int:
        """nu(l'): structural components the candidate touches that the incumbent has
        never had an accepted edit on. Only ever tie-breaks inside the noise band."""
        return sum(1 for c in set(edit_components) if c in self.K_str and incumbent_counts.get(c, 0) == 0)


# ---- module-level functions with the released code's signatures (default taxonomy) --------------
_DEFAULT = Taxonomy()


def classify_diff(diff: str, domain_signals: Optional[list] = None) -> str:
    return Taxonomy(K, K_STR, {}, domain_signals).classify(diff)


def has_evidence(component: str, diff: str, domain_signals: Optional[list] = None) -> bool:
    return Taxonomy(K, K_STR, {}, domain_signals).has_evidence(component, diff)


def normalize(declared: Optional[str], diff: str, domain_signals: Optional[list] = None) -> str:
    return Taxonomy(K, K_STR, {}, domain_signals).normalize(declared, diff)


def novelty(edit_components: Iterable[str], incumbent_counts: dict, structural: Sequence[str] = tuple(K_STR)) -> int:
    return sum(1 for c in set(edit_components) if c in structural and incumbent_counts.get(c, 0) == 0)
