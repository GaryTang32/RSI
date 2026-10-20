"""Component vocabulary K, structural subset K_str, and diff-evidenced tagging of declared edits."""
from __future__ import annotations

import fnmatch
import re
from typing import Iterable, Optional, Sequence

K = ["prompt", "control_flow", "config", "output_plumbing", "context_mgmt",
     "client_tool", "skill", "memory", "subagent"]
K_STR = ["client_tool", "skill", "memory", "subagent"]

GENERIC_SIGNALS: list[tuple[str, list[str]]] = [
    ("memory", [r"\bMemory\(", r"\.remember\(", r"\.recall\(", r"_STATE_DIR"]),
    ("skill", [r"skills/", r"SkillRegistry", r"skill_use", r"skill_catalog", r"upload_skills"]),
    ("client_tool", [r"ToolRegistry", r"register_tool", r"tool_spec", r"CLIENT_TOOLS"]),
    ("subagent", [r"\bsubcall\(", r"sub_agent", r"subagent"]),
]

# Maps the generic signals onto a domain's names; accepts a declared alias only with ``aliases=True``.
ALIASES = {"client_tool": "tool", "tool": "client_tool", "tools": "tool", "prompts": "prompt",
           "control": "control_flow", "skills": "skill", "subagents": "subagent", "agents": "subagent"}

_STRING_LINE = re.compile(r'^[+-]\s*(?:[frb]?["\']|""")')
_PATH_LINE = re.compile(r"^(?:\+\+\+|---) (?:[ab]/)?(\S+)", re.M)


def changed_lines(diff: str) -> list[str]:
    return [l for l in (diff or "").splitlines()
            if (l.startswith("+") or l.startswith("-")) and not l.startswith("+++") and not l.startswith("---")
            and l[1:].strip()]


def text_only(diff: str) -> bool:
    """Every changed line is a string literal or a comment (a ``prompt`` edit)."""
    ch = changed_lines(diff)
    return bool(ch) and all(_STRING_LINE.match(l) or l[1:].lstrip().startswith("#") for l in ch)


def changed_paths(diff: str) -> list[str]:
    """Files named in the unified-diff headers."""
    return list(dict.fromkeys(p for p in _PATH_LINE.findall(diff or "") if p != "/dev/null"))


class Taxonomy:
    """K, K_str and the evidence signals of one domain."""

    def __init__(self, components: Sequence[str] = tuple(K), structural: Sequence[str] = tuple(K_STR),
                 path_hints: Optional[dict[str, list[str]]] = None,
                 signals: Optional[list[tuple[str, list[str]]]] = None, *, trust_code_tags: bool = False,
                 aliases: bool = False) -> None:
        self.aliases = aliases
        self.K = list(components)
        self.K_str = [c for c in structural if c in self.K]
        self.path_hints = {c: list(v) for c, v in (path_hints or {}).items() if c in self.K}
        self.signals = [(c, list(p)) for c, p in (signals or []) if c in self.K]
        self.trust_code_tags = trust_code_tags
        # path hints: structural first, prompt last
        order = self.K_str + [c for c in self.K if c not in self.K_str and c != "prompt"] + \
            (["prompt"] if "prompt" in self.K else [])
        self._path_order = [c for c in order if c in self.path_hints]

    @classmethod
    def from_domain(cls, domain, *, aliases: bool = False) -> "Taxonomy":
        comps = dict(getattr(domain, "components", None) or {})
        if not comps:
            sig = list(getattr(domain, "component_signals", []) or [])
            return cls(K, K_STR, {}, sig, trust_code_tags=not sig, aliases=aliases)
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
        return cls(list(comps), structural, paths, regs, aliases=aliases)

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
        if self._path_match(component, changed_paths(diff)):
            return True
        if any(comp == component and any(re.search(p, diff) for p in pats)
               for comp, pats in self.signals + self._generic()):
            return True
        if self.trust_code_tags and component in self.K and component not in self.K_str:
            return component == "prompt" or (bool(changed_lines(diff)) and not text_only(diff))
        return False

    def classify(self, diff: str) -> str:
        """First match of domain signals, path hints, generic signals; else prompt (control_flow for trusted code)."""
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

    def canonical(self, declared: Optional[str], strip: bool = True) -> str:
        """Lower-cased (and stripped) tag; an alias maps into K only with ``aliases=True``."""
        d = str(declared or "").lower()
        if strip:
            d = d.strip()
        if self.aliases and d not in self.K and ALIASES.get(d.strip()) in self.K:
            d = ALIASES[d.strip()]
        return d

    def normalize(self, declared: Optional[str], diff: str) -> str:
        """The declared tag if in K and evidenced by the (whole candidate) diff, else :meth:`classify`."""
        d = self.canonical(declared)
        return d if d in self.K and self.has_evidence(d, diff) else self.classify(diff)

    def novelty(self, edit_components: Iterable[str], incumbent_counts: dict) -> int:
        """nu(l'): structural components touched that have no accepted edit in the incumbent."""
        return sum(1 for c in set(edit_components) if c in self.K_str and incumbent_counts.get(c, 0) == 0)


def normalize(declared: Optional[str], diff: str, domain_signals: Optional[list] = None) -> str:
    return Taxonomy(K, K_STR, {}, domain_signals).normalize(declared, diff)


def novelty(edit_components: Iterable[str], incumbent_counts: dict, structural: Sequence[str] = tuple(K_STR)) -> int:
    return sum(1 for c in set(edit_components) if c in structural and incumbent_counts.get(c, 0) == 0)
