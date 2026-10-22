"""Deterministic mock reflection LM for RuleWorld: sees only its prompt and the world lexicon."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Optional

from ...core.llm import MockLLM
from ...gepa.reflection import parse_side_info
from .world import GARBLED, STANDARD, TICKET_RE, RuleWorld, _h

RULE_RE = re.compile(r"Rule:\s*When an order is (\w+), use the (\w+) protocol", re.I)
SYMPTOM_RE = re.compile(r"The (\w+) property was handled incorrectly", re.I)
SCORE_RE = re.compile(r"Score:\s*(\d+(?:\.\d+)?)")
PAIR_RE = re.compile(r"(\w+):\s*(\w+)")
GROUNDED_MARKER = "correct outputs for several example inputs"


@dataclass
class ReflectionProfile:
    max_new: int = 2           # new rule lines per call
    q_copy: float = 0.15       # P(write a hint as a ticket-specific fact: the overfitting lure)
    r_drop: float = 0.05       # P(drop one existing rule line)
    p_insight: float = 0.4     # P(condition a contradicting rule on the ticket's family)
    p_replace: float = 0.6     # else P(replace the contradicted general rule)
    max_lines: int = 40
    p_infer: float = 0.6       # grounded proposals: P(infer a rule from a shown pair)
    p_scope: float = 0.1       # P(scope a brand-new rule to the ticket's family)
    p_diagnose: float = 0.25   # P(restrict another rule whose protocol leaked into this property)

    def to_json(self) -> dict:
        return asdict(self)


def split_fenced(prompt: str) -> tuple[str, str]:
    """(current instruction, side_info) from a GEPA-style reflection prompt."""
    parts = prompt.split("```")
    cur = parts[1].strip("\n") if len(parts) > 1 else ""
    side = parts[3] if len(parts) > 3 else ""
    return cur, side


class RuleWorldReflectionLM(MockLLM):
    """Mock reflection LM for :class:`~rsi.domains.ruleworld.RuleWorld`."""

    def __init__(self, world: RuleWorld, profile: Optional[ReflectionProfile] = None, name: str = "ruleworld-reflect"):
        self.world = world
        self.profile = profile or ReflectionProfile()
        super().__init__(self._respond, name=name)

    def _respond(self, prompt: str, system, seed, i) -> str:
        rng = _h("reflect", prompt, seed)
        if GROUNDED_MARKER in prompt:
            return self._grounded(prompt, rng)
        cur, side = split_fenced(prompt)
        return "```\n" + self.reflect(cur, parse_side_info(side), rng) + "\n```"

    def _family(self, text: str) -> Optional[str]:
        m = re.search(r"\((\w+) customer\)", text)
        return m.group(1).lower() if m and m.group(1).lower() in self.world.families else None

    def _hints(self, recs: list[dict], rng) -> list[dict]:
        aspects = self.world.aspects
        hints = []
        for r in recs:
            fb = r.get("Feedback", "")
            m = SCORE_RE.search(fb)
            if m and float(m.group(1)) >= 1.0:
                continue
            inputs, gen = r.get("Inputs", ""), r.get("Generated Outputs", "")
            fam, tk = self._family(inputs), (TICKET_RE.findall(inputs) or [None])[0]
            outs = {a.lower(): c.lower() for a, c in PAIR_RE.findall(gen.split("\nTrace:")[0]) if a.lower() in aspects}
            named = [(a.lower(), c.lower()) for a, c in RULE_RE.findall(fb)
                     if a.lower() in aspects and c.lower() in self.world.code_owner]
            if named:
                pairs = named
            else:
                # garbled outputs are visibly noise: nothing to learn from them
                sym = [a.lower() for a in SYMPTOM_RE.findall(fb) if a.lower() in aspects
                       and outs.get(a.lower()) != GARBLED]
                if sym:
                    pairs = [(a, None) for a in sym]
                else:  # score only: guess the aspect, preferring outputs with the standard protocol
                    live = [a for a, c in outs.items() if c != GARBLED]
                    cands = [a for a in live if outs[a] == STANDARD] or live
                    if not cands or SYMPTOM_RE.search(fb):
                        continue
                    pairs = [(rng.choice(sorted(cands)), None)]
            hints += [{"aspect": a, "code": c, "family": fam, "ticket": tk, "outs": outs, "named": dict(named)}
                      for a, c in pairs]
        return hints

    def reflect(self, cur: str, recs: list[dict], rng) -> str:
        p, w = self.profile, self.world
        lines = cur.splitlines()
        hints = self._hints(recs, rng)
        counts: dict[str, int] = {}
        for h in hints:
            counts[h["aspect"]] = counts.get(h["aspect"], 0) + 1
        for a in sorted(counts, key=lambda a: -counts[a])[: p.max_new]:      # most frequent first, ties by first seen
            h = next(x for x in hints if x["aspect"] == a)
            got = h["outs"].get(a)
            code = h["code"] or rng.choice([c for c in w.aspects[a].codes if c != got])   # never the failing one
            if h["ticket"] and rng.random() < p.q_copy:
                known = {**h["outs"], **h["named"], a: code}
                lines.append(f"Ticket {h['ticket']}: " + "; ".join(f"{k}: {v}" for k, v in known.items()))
                continue
            self._add_rule(lines, a, code, h["family"], rng, got=got)
        rule_idx = [j for j, l in enumerate(lines) if self._is_rule_line(l)]
        if rule_idx and rng.random() < p.r_drop:
            del lines[rng.choice(rule_idx)]
        while len([l for l in lines if l.strip()]) > p.max_lines:
            rule_idx = [j for j, l in enumerate(lines) if self._is_rule_line(l)]
            if not rule_idx:
                break
            del lines[rule_idx[0]]
        return "\n".join(lines).strip("\n")

    def _is_rule_line(self, line: str) -> bool:
        v = self.world.parse_module(line)
        return bool(v.rules or v.facts or v.demos)

    def _rules_in(self, line: str, aspect: str) -> list[tuple[str, frozenset]]:
        return self.world.parse_module(line).rules.get(aspect, [])

    def _add_rule(self, lines: list[str], a: str, code: str, fam: Optional[str], rng,
                  got: Optional[str] = None) -> None:
        p, w = self.profile, self.world
        general_same, general_other, scoped, covered = [], [], 0, False
        for j, l in enumerate(lines):
            for c, fams in self._rules_in(l, a):
                if not fams:
                    (general_same if c == code else general_other).append(j)
                else:
                    scoped += 1
                    covered = covered or (fam in fams and c == code)
        if covered or (general_same and not general_other):
            # the rule is already there: noise, or a side effect of another rule
            owner = w.code_owner.get(got or "")
            if fam and owner and owner != a and rng.random() < p.p_diagnose:
                rest = [f for f in w.families if f != fam]
                for j, l in enumerate(lines):
                    for c, fams in self._rules_in(l, owner):
                        if not fams:
                            lines[j] = w.rule_text(owner, c, rest)
            return
        if general_other:
            if fam and rng.random() < p.p_insight:
                lines.append(w.rule_text(a, code, [fam]))
            elif rng.random() < p.p_replace:
                for j in sorted(set(general_other), reverse=True):
                    del lines[j]
                if not general_same:
                    lines.append(w.rule_text(a, code))
            elif not general_same:
                lines.append(w.rule_text(a, code))
            return
        scope = fam and (scoped or rng.random() < p.p_scope)
        lines.append(w.rule_text(a, code, [fam]) if scope else w.rule_text(a, code))

    def _grounded(self, prompt: str, rng) -> str:
        """Few-shot instruction proposal: infer rules from gold (input, correct output) examples."""
        p, w = self.profile, self.world
        cur, body = split_fenced(prompt)
        lines = cur.splitlines()
        evidence: dict[str, dict[str, set]] = {}
        for m in re.finditer(r"Input:\s*(.+?)\nCorrect output:\s*(.+)", body):
            fam = self._family(m.group(1))
            for a, c in PAIR_RE.findall(m.group(2)):
                a, c = a.lower(), c.lower()
                if a in w.aspects and c in w.code_owner and rng.random() < p.p_infer:
                    evidence.setdefault(a, {}).setdefault(c, set()).add(fam)
        for a in sorted(evidence):
            codes = evidence[a]
            if len(codes) == 1:
                lines.append(w.rule_text(a, next(iter(codes))))
            elif rng.random() < p.p_insight:
                for c in sorted(codes):
                    fams = sorted(f for f in codes[c] if f)
                    lines.append(w.rule_text(a, c, fams) if fams else w.rule_text(a, c))
            else:
                lines.append(w.rule_text(a, max(sorted(codes), key=lambda c: len(codes[c]))))
        return "```\n" + "\n".join(lines).strip("\n") + "\n```"
