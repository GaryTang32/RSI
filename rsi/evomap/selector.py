"""Gene scoring and selection (spec §3.3, §4.2, §4.3, §4.5).

:class:`GeneScorer`
    ``mode="spec"``: score = #matching ``signals_match`` patterns.
    ``mode="current"``: base = hits + 0.6*tag + 0.4*cosine (tag = |geneTags ∩
    expandSignals(signals)|), adj = learning history (+0.12 success, -0.22 hard,
    -0.08 soft over the last 8) + epigenetic boost - anti-pattern penalties
    (0.4 hard / 0.18 soft over the last 6 that share a learning signal),
    clamped to [-1.5, 1.5]; distilled genes x0.8.

:func:`drift_intensity`
    I = min(1, 1/sqrt(Ne) + offset) with maturity-dependent offset when drift is
    on (0.7 for Ne <= 1), I = min(1, 1/sqrt(Ne)) otherwise; plateau raises I to
    0.7 / 1.0. With probability I the selector picks uniformly among the top
    n = min(|cands|, max(2, ceil(|cands|*I))).

:class:`GeneSelector`
    bans = memory-graph bans ∪ failed-capsule bans (>= 2 failed capsules whose
    trigger overlaps the signals >= 0.6) ∪ ``ban_gene:<id>`` signals; forced
    gene; preferred gene x1.5 (not on a plateau); epigenetic suppression
    (boost <= -0.3 in this environment); distilled fallback; capsule chosen by
    trigger match independently of the gene.
"""
from __future__ import annotations

import math
import random
import re
from dataclasses import dataclass, field
from typing import Optional, Sequence

from .assets import Capsule, Gene
from .memory import Advice
from .signals import (PlateauOverride, cosine, expand_signals, match_pattern, pattern_hits, tokens)

EPIGENETIC_HARD_BOOST = -0.3
DISTILLED_FACTOR = 0.8
PREFERRED_FACTOR = 1.5


def gene_tags(g: Gene) -> set[str]:
    return expand_signals([f"action:{g.category}", *g.signals_match, g.id, *tokens(g.summary)])


def epigenetic_boost(g: Gene, env: Optional[str]) -> float:
    if not env:
        return 0.0
    for m in reversed(g.epigenetic_marks or []):
        if isinstance(m, dict) and m.get("context") == env:
            return float(m.get("boost", 0.0))
    return 0.0


def suppressed(g: Gene, env: Optional[str]) -> bool:
    return epigenetic_boost(g, env) <= EPIGENETIC_HARD_BOOST


class GeneScorer:
    def __init__(self, mode: str = "current", semantic_weight: float = 0.4, require_match: bool = True) -> None:
        self.mode = mode
        self.w_sem = semantic_weight
        #: port deviation (documented): a gene with no pattern hit and no token overlap scores 0, so tag
        #: overlap or learning history alone cannot make an irrelevant gene a candidate. False = engine rule.
        self.require_match = require_match

    def relevant(self, g: Gene, signals: Sequence[str]) -> bool:
        if pattern_hits(g.signals_match, signals) > 0:
            return True
        return self.mode == "current" and cosine(tokens(" ".join(signals)),
                                                 tokens(" ".join([*g.signals_match, g.summary, g.id]))) > 0

    def base(self, g: Gene, signals: Sequence[str]) -> float:
        hits = pattern_hits(g.signals_match, signals)
        if self.mode == "spec":
            return float(hits)
        ex = expand_signals(signals)
        tag = len(gene_tags(g) & ex)
        if not g.signals_match:
            return 0.6 * tag
        cos = cosine(tokens(" ".join(signals)), tokens(" ".join([*g.signals_match, g.summary, g.id])))
        return hits + 0.6 * tag + self.w_sem * cos

    def adjustment(self, g: Gene, signals: Sequence[str], env: Optional[str]) -> float:
        if self.mode == "spec":
            return 0.0
        adj = 0.0
        for h in (g.learning_history or [])[-8:]:
            if h.get("outcome") == "success":
                adj += 0.12
            elif h.get("mode") == "hard":
                adj -= 0.22
            else:
                adj -= 0.08
        adj += epigenetic_boost(g, env)
        ex = expand_signals(signals)
        for a in (g.anti_patterns or [])[-6:]:
            if set(a.get("learning_signals", [])) & ex:
                adj -= 0.4 if a.get("mode") == "hard" else 0.18
        return max(-1.5, min(1.5, adj))

    def score(self, g: Gene, signals: Sequence[str], env: Optional[str] = None) -> float:
        if self.require_match and not self.relevant(g, signals):
            return 0.0
        b = self.base(g, signals)
        s = b + self.adjustment(g, signals, env)
        if s > 0 and g.id.startswith("gene_distilled_"):
            s *= DISTILLED_FACTOR
        return s


def drift_intensity(ne: int, drift_enabled: bool, memory_evidence: int = 0) -> float:
    if drift_enabled:
        if ne <= 1:
            return 0.7
        maturity = min(1.0, memory_evidence / (ne * 10.0))
        offset = 0.3 - (0.3 - 0.02) * maturity
        return min(1.0, 1.0 / math.sqrt(ne) + offset)
    return min(1.0, 1.0 / math.sqrt(ne)) if ne > 0 else 0.0


def ban_from_failed_capsules(failed: Sequence[dict], signals: Sequence[str], min_overlap: float = 0.6,
                             min_count: int = 2, successes: Optional[dict] = None) -> set[str]:
    """Genes with >= ``min_count`` failed capsules whose trigger overlaps the signals >= 0.6.
    ``successes`` (gene -> successful solidifies) switches on the *relative* variant used in
    safe mode: the failures must also outnumber the gene's successes (stochastic graders)."""
    sig = set(signals)
    counts: dict = {}
    for fc in failed:
        trig = set(fc.get("trigger") or fc.get("learning_signals") or [])
        if not sig:
            continue
        if len(sig & trig) / len(sig) >= min_overlap:
            gid = fc.get("gene")
            if gid:
                counts[gid] = counts.get(gid, 0) + 1
    return {g for g, n in counts.items() if n >= min_count and (successes is None or n > successes.get(g, 0))}


BAN_RE = re.compile(r"^ban_gene:([A-Za-z0-9][A-Za-z0-9_\-]{0,127})$")


@dataclass
class SelectorDecision:
    gene: Optional[Gene]
    capsule: Optional[Capsule] = None
    alternatives: list[str] = field(default_factory=list)
    mode: str = "none"               # selection | random | random_weighted | diversity_directed | forced_gene | ...
    reasons: list[str] = field(default_factory=list)
    scores: dict = field(default_factory=dict)
    banned: set = field(default_factory=set)
    drift_intensity: float = 0.0
    memory_used: bool = False


class GeneSelector:
    def __init__(self, scorer: Optional[GeneScorer] = None, *, use_memory: bool = True,
                 distilled_fallback: bool = True) -> None:
        self.scorer = scorer or GeneScorer()
        self.use_memory = use_memory
        self.distilled_fallback = distilled_fallback

    def select(self, genes: Sequence[Gene], capsules: Sequence[Capsule], signals: Sequence[str], *,
               advice: Optional[Advice] = None, failed_capsules: Sequence[dict] = (), rng: Optional[random.Random] = None,
               drift_enabled: bool = False, plateau: Optional[PlateauOverride] = None, env: Optional[str] = None,
               forced_gene_id: Optional[str] = None, capability_gaps: Sequence[str] = (),
               novelty: float = 1.0, gene_successes: Optional[dict] = None) -> SelectorDecision:
        rng = rng or random.Random(0)
        advice = advice if (advice is not None and self.use_memory) else Advice()
        bans = set(advice.banned_gene_ids)
        bans |= ban_from_failed_capsules(list(failed_capsules)[-50:], signals, successes=gene_successes)
        bans |= {m.group(1) for s in signals for m in [BAN_RE.match(s)] if m}
        dec = SelectorDecision(None, banned=bans, memory_used=advice.total_attempts > 0)
        by_id = {g.id: g for g in genes}
        if forced_gene_id and forced_gene_id in by_id and forced_gene_id not in bans \
                and not suppressed(by_id[forced_gene_id], env):
            dec.gene, dec.mode = by_id[forced_gene_id], "forced_gene"
            dec.capsule = self.best_capsule(capsules, signals)
            return dec
        scored = []
        for g in genes:
            if suppressed(g, env):
                continue
            s = self.scorer.score(g, signals, env)
            dec.scores[g.id] = s
            if s > 0:
                scored.append([g, s])
        scored.sort(key=lambda x: (-x[1], x[0].id))
        if not scored:
            if self.distilled_fallback:
                for g in genes:
                    if g.id.startswith("gene_distilled_") and g.id not in bans and not suppressed(g, env):
                        dec.gene, dec.mode = g, "distilled_fallback"
                        break
            dec.capsule = self.best_capsule(capsules, signals)
            return dec
        plateau_active = bool(plateau and plateau.active)
        pref = advice.preferred_gene_id
        if pref and not plateau_active and pref not in bans:
            for x in scored:
                if x[0].id == pref:
                    x[1] *= PREFERRED_FACTOR
                    dec.reasons.append(f"memory preferred {pref}")
            scored.sort(key=lambda x: (-x[1], x[0].id))
        cands = [x for x in scored if x[0].id not in bans]
        if not cands:
            dec.reasons.append("all matching genes banned")
            dec.capsule = self.best_capsule(capsules, signals)
            return dec
        I = drift_intensity(len(genes), drift_enabled, advice.total_attempts)
        if plateau_active:
            I = max(I, 1.0 if plateau.severity == "required" else 0.7)
        dec.drift_intensity = I
        idx, mode = 0, "selection"
        if I > 0 and len(cands) > 1 and rng.random() < I:
            n = min(len(cands), max(2, math.ceil(len(cands) * I)))
            gaps = list(capability_gaps)[:5]
            if gaps:
                hits = [sum(1 for gp in gaps if match_pattern(gp, c[0].signals_match) or
                            any(match_pattern(p, [gp]) for p in c[0].signals_match)) for c in cands]
                if max(hits) > 0:
                    idx = max(range(len(cands)), key=lambda i: (hits[i], cands[i][1]))
                    mode = "diversity_directed"
                else:
                    if novelty < 0.3 and n < len(cands):
                        n += 1
                    idx, mode = rng.randrange(n), "random_weighted"
            else:
                idx, mode = rng.randrange(n), "random"
        dec.gene, dec.mode = cands[idx][0], mode
        dec.alternatives = [c[0].id for i, c in enumerate(cands) if i != idx][:4]
        dec.capsule = self.best_capsule(capsules, signals)
        return dec

    @staticmethod
    def best_capsule(capsules: Sequence[Capsule], signals: Sequence[str]) -> Optional[Capsule]:
        best, best_n = None, 0
        for c in capsules:
            n = sum(1 for t in c.trigger if match_pattern(t, signals))
            if n > best_n:
                best, best_n = c, n
        return best
