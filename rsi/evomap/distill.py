"""Distillation (capsules -> genes) and the leakage audit (spec §3.7, §4.14, §6.4).

:class:`Distiller`
    * triggers: every 5th successful solidify (bypasses the other rules) or
      ``should_distill`` (24 h since last, >= 7 successes in the last 10 capsules,
      >= 10 successful capsules);
    * :meth:`auto_distill` - the default heuristic synthesizer (no LLM): group
      good capsules (score >= 0.7) by gene, pick argmax(2*count + avg score),
      build ``gene_distilled_<src>`` from the top trigger tokens and the source
      gene's first 4 steps; validation = the source gene's (<= 4) else the
      faithful fallback ``["python --version"]`` (Evolver: ``node --version``) or,
      in safe mode, the source's discriminative checks only;
    * :meth:`llm_distill` - the §6.4 prompt fallback;
    * :meth:`distill_from_failures` - a defensive "repair gene" from >= 5 failed
      capsules with a recurring pattern;
    * :meth:`validate_synth` - the synthesized-gene validator rules.

:class:`LeakageAuditor`
    skill2gep's mechanical audit: literals that appear in *hidden* text (the
    graded target, hidden feedback, the final solution) but not in the public
    task text are private; they are redacted from every string of the gene.
    Also screens with :class:`rsi.core.LeakageCritic` denylist terms.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

from rsi.core import LeakageCritic

from .assets import CATEGORIES, Gene
from .hashing import sha256_text
from .prompts import (DISTILL_PROMPT, DISTILL_SYSTEM, DISTILL_VALIDATION_FAITHFUL, DISTILL_VALIDATION_SAFE,
                      parse_gene)
from .signals import tokens

GENERIC_STEPS = ["Identify the failing behaviour from the signals and the public checks.",
                 "Apply the smallest change that addresses the root cause.",
                 "Re-run the validation commands and compare against the expected behaviour.",
                 "Record what worked and what to avoid next time."]


@dataclass
class DistillResult:
    ok: bool
    gene: Optional[Gene] = None
    reason: str = ""
    errors: list[str] = field(default_factory=list)


class Distiller:
    def __init__(self, *, mode: str = "faithful", every: int = 5, min_capsules: int = 10, min_score: float = 0.7,
                 interval_h: float = 24.0, failure_min: int = 5, failure_interval_h: float = 12.0,
                 fallback_validation: Sequence[str] = ("python --version",), command_policy=None) -> None:
        self.mode = mode
        self.every = every
        self.min_capsules = min_capsules
        self.min_score = min_score
        self.interval_h = interval_h
        self.failure_min = failure_min
        self.failure_interval_h = failure_interval_h
        self.fallback_validation = list(fallback_validation)
        self.policy = command_policy
        self.last_at: Optional[float] = None
        self.last_hash: Optional[str] = None
        self.last_fail_at: Optional[float] = None
        self.last_fail_hash: Optional[str] = None

    # ------------------------------------------------------------------ triggers
    def auto_trigger(self, store) -> bool:
        return store.solidify_count > 0 and self.every > 0 and store.solidify_count % self.every == 0

    def should_distill(self, store) -> bool:
        now = store.clock.now()
        if self.last_at is not None and now - self.last_at < self.interval_h * 3600:
            return False
        caps = list(store.capsules.values())
        last10 = caps[-10:]
        good = [c for c in caps if c.outcome.get("status") == "success" and c.outcome.get("score", 1) >= self.min_score]
        return sum(1 for c in last10 if c.outcome.get("status") == "success") >= 7 and len(good) >= self.min_capsules

    def maybe_distill(self, store, *, llm=None) -> Optional[DistillResult]:
        """Run after a SUCCESSFUL solidify (CLI-handler timing)."""
        res = None
        if self.auto_trigger(store) or self.should_distill(store):
            res = self.auto_distill(store)
            if (res is None or not res.ok) and llm is not None:
                res = self.llm_distill(store, llm)
        return res

    # ------------------------------------------------------------------ heuristic synthesizer
    def auto_distill(self, store) -> DistillResult:
        good = [c for c in store.capsules.values()
                if c.outcome.get("status") == "success" and float(c.outcome.get("score", 1.0)) >= self.min_score]
        if len(good) < self.min_capsules:
            return DistillResult(False, reason=f"only {len(good)} good capsules (< {self.min_capsules})")
        h = sha256_text("|".join(sorted(c.asset_id or c.id for c in good)))
        if h == self.last_hash:
            return DistillResult(False, reason="data hash unchanged")
        groups: dict[str, list] = defaultdict(list)
        for c in good:
            groups[c.gene].append(c)
        gid, caps = max(groups.items(), key=lambda kv: (2 * len(kv[1]) + sum(c.outcome.get("score", 0) for c in kv[1])
                                                       / len(kv[1]), kv[0]))
        src = store.genes.get(gid)
        trig = Counter(t for c in caps for t in c.trigger if not t.startswith(("problem:", "area:")))
        tags = Counter(t for c in caps for t in c.trigger if t.startswith(("problem:", "area:")))
        sm = [t for t, _ in trig.most_common(6)] + [t for t, _ in tags.most_common(4)]
        strategy = list(src.strategy[:4]) if src is not None and src.strategy else list(GENERIC_STEPS)
        if src is not None and src.validation:
            val = list(src.validation[:4])
        else:
            val = list(self.fallback_validation)
        if self.mode == "safe":
            # never let the distiller introduce a non-discriminative fallback
            val = [v for v in val if "--version" not in v] or ([] if src is None else list(src.validation[:4]))
        g = Gene(id="gene_distilled_" + re.sub(r"^gene_", "", gid), category=src.category if src else "optimize",
                 signals_match=sm, strategy=strategy, avoid=list(src.avoid[:3]) if src else [],
                 summary=caps[0].summary, constraints={"max_files": min(12, src.max_files if src else 12),
                                                       "forbidden_paths": [".git", "node_modules"]},
                 validation=val, provenance={"kind": "distilled", "source_gene": gid, "n_capsules": len(caps)},
                 parent=src.asset_id if src else None)
        r = self.validate_synth(g, store.genes.values())
        if r.ok:
            self.last_hash = h
            self.last_at = store.clock.now()
            store.upsert_gene(r.gene)
            store.distiller_log.append({"at": store.clock.iso(), "gene": r.gene.id, "source": gid, "n": len(caps)})
        return r

    # ------------------------------------------------------------------ LLM fallback
    def llm_distill(self, store, llm) -> DistillResult:
        caps = [c for c in store.capsules.values() if c.outcome.get("status") == "success"][-10:]
        if not caps:
            return DistillResult(False, reason="no capsules")
        body = "\n".join(f"- gene={c.gene} trigger={c.trigger[:6]} summary={c.summary[:160]} "
                         f"strategy={c.strategy[:4]}" for c in caps)
        rule = DISTILL_VALIDATION_SAFE if self.mode == "safe" else DISTILL_VALIDATION_FAITHFUL
        resp = llm.complete(DISTILL_PROMPT.format(validation_rule=rule, capsules=body), system=DISTILL_SYSTEM,
                            role="distiller")
        g = parse_gene(resp.text, default_id="gene_distilled_llm")
        if g is None:
            return DistillResult(False, reason="unparseable distiller reply")
        g.provenance = {"kind": "distilled", "generation_source": "llm"}
        r = self.validate_synth(g, store.genes.values())
        if r.ok:
            store.upsert_gene(r.gene)
            store.distiller_log.append({"at": store.clock.iso(), "gene": r.gene.id, "source": "llm"})
        return r

    # ------------------------------------------------------------------ failure distillation
    def distill_from_failures(self, store) -> DistillResult:
        fcs = store.failed_capsules
        if len(fcs) < self.failure_min:
            return DistillResult(False, reason="too few failed capsules")
        now = store.clock.now()
        if self.last_fail_at is not None and now - self.last_fail_at < self.failure_interval_h * 3600:
            return DistillResult(False, reason="failure distiller interval")
        h = sha256_text("|".join(fc["id"] for fc in fcs))
        if h == self.last_fail_hash:
            return DistillResult(False, reason="failure data hash unchanged")
        pat = Counter(r for fc in fcs for r in fc.get("failure_reason", []))
        recurring = [p for p, n in pat.items() if n >= 2]
        if not recurring:
            return DistillResult(False, reason="no recurring failure pattern")
        sig = Counter(s for fc in fcs for s in fc.get("learning_signals", []))
        top = recurring[0]
        g = Gene(id="gene_repair_distilled_" + re.sub(r"[^a-z0-9]+", "_", top.lower())[:40].strip("_"),
                 category="repair", signals_match=[s for s, _ in sig.most_common(5)],
                 strategy=[f"Verify the preconditions that previously led to: {top}.",
                           "Apply the safe, minimal action only after the guard passes.",
                           "Run the validation commands before and after the change."],
                 avoid=[f"Repeating the change that caused: {top}"],
                 summary=f"Prevents {top} by ensuring guards pass before acting",
                 constraints={"max_files": 8, "forbidden_paths": [".git", "node_modules"]},
                 validation=[], provenance={"kind": "distilled", "from": "failures"})
        r = self.validate_synth(g, store.genes.values())
        if r.ok:
            self.last_fail_at, self.last_fail_hash = now, h
            store.upsert_gene(r.gene)
        return r

    # ------------------------------------------------------------------ validator
    def validate_synth(self, g: Gene, genes: Iterable[Gene]) -> DistillResult:
        errs = []
        if g.category not in CATEGORIES:
            errs.append("invalid category")
        g.signals_match = [s for s in g.signals_match if not re.fullmatch(r"\d{6,}|.*\d{10,}.*", s)]
        if not g.signals_match:
            errs.append("empty signals_match")
        if not g.strategy:
            errs.append("empty strategy")
        g.summary = re.sub(r"\s*\d{10,}\s*$", "", g.summary or "")
        prefix = "gene_repair_distilled_" if g.id.startswith("gene_repair_distilled_") else "gene_distilled_"
        if not g.id.startswith(prefix):
            g.id = prefix + re.sub(r"^gene_", "", g.id)
        suffix = re.sub(r"\d+", "", g.id[len(prefix):]).strip("_-")
        if len(suffix) < 6:
            g.id = prefix + (suffix + "_" + sha256_text(g.text())[:8]).strip("_")
        if len(g.summary) < 10:
            g.summary = g.strategy[0] if g.strategy else g.summary
        if len(g.strategy) < 3:
            errs.append("strategy has fewer than 3 steps")
        fp = g.forbidden_paths
        if ".git" not in fp and "node_modules" not in fp:
            errs.append("forbidden_paths must contain .git or node_modules")
        g.constraints["max_files"] = min(12, int(g.constraints.get("max_files", 12)))
        if self.policy is not None:
            g.validation = [v for v in g.validation if self.policy.check(v).ok]   # an EMPTY result is still accepted
        existing = list(genes)
        ids = {x.id for x in existing}
        base_id, k = g.id, 1
        while g.id in ids:
            k += 1
            g.id = f"{base_id}_{k}"
        for x in existing:
            if set(x.signals_match) == set(g.signals_match):
                errs.append(f"signals_match fully overlaps {x.id}")
                break
        return DistillResult(not errs, g if not errs else None, "ok" if not errs else errs[0], errs)


# ----------------------------------------------------------------------------- leakage audit
_NUM = re.compile(r"-?\d+(?:\.\d+)?")
_QUOTED = re.compile(r"['\"]([^'\"]{2,64})['\"]")
_STRUCT = re.compile(r"(?<![A-Za-z0-9_./\\-])[A-Za-z0-9][A-Za-z0-9_./\\-]{1,80}(?![A-Za-z0-9_./\\-])")
AUDIT_STOP = {"the", "and", "for", "with", "that", "this", "from", "into", "your", "you", "are", "not", "use", "using",
              "must", "should", "will", "can", "may", "any", "all", "each", "one", "two", "given", "input", "output",
              "value", "values", "result", "results", "return", "returns", "function", "code", "task", "tasks", "test",
              "tests", "assert", "import", "def", "class", "self", "true", "false", "none", "print", "data", "list",
              "dict", "string", "str", "int", "float", "bool", "file", "path", "line", "answer", "solution",
              "python", "run", "expected", "actual", "case", "example", "number", "set", "get", "name", "format"}


def _trivial_number(t: str) -> bool:
    return "." not in t and re.fullmatch(r"-?\d+", t) is not None and abs(int(t)) < 10


def _structured(t: str) -> bool:
    if re.fullmatch(r"-?\d+(?:\.\d+)?", t):
        return not _trivial_number(t)
    return t.startswith("--") or bool(re.search(r"[0-9_./\\]", t)) or bool(re.fullmatch(r"[A-Z][A-Z0-9_-]{2,}", t))


def hard_tokens(text: str, bare: bool = False) -> set[str]:
    t = str(text or "")
    out = {m for m in _NUM.findall(t) if not _trivial_number(m)}
    for m in _QUOTED.findall(t):
        q = m.strip().lower()
        if len(re.findall(r"[a-z0-9]", q)) >= 3 and q not in AUDIT_STOP and _structured(q):
            out.add(q)
    if bare:
        for m in _STRUCT.findall(t):
            tok = m.strip("`'\".,:;()[]{}<>").lower()
            if len(re.findall(r"[a-z0-9]", tok)) >= 2 and tok not in AUDIT_STOP and _structured(tok):
                out.add(tok)
    return out


@dataclass
class LeakageReport:
    leaks: list[tuple[str, str]] = field(default_factory=list)      # (token, location)
    redacted: int = 0
    critic_hits: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.leaks and not self.critic_hits


class LeakageAuditor:
    def __init__(self, deny_terms: Iterable[str] = (), min_term_len: int = 4) -> None:
        self.critic = LeakageCritic(deny_terms, min_term_len=min_term_len)
        self._terms = [(t, str(t).lower()) for t in self.critic.terms]

    def deny_hits(self, text: str) -> list[str]:
        """rsi.core LeakageCritic whole-token denylist semantics, with a substring prefilter so only
        candidate terms compile a regex (the hub's bank has hundreds of terms)."""
        hay = text.lower()
        cand = [t for t, low in self._terms if low in hay]
        if not cand:
            return []
        return LeakageCritic(cand, min_term_len=0).denylist_hits("\n".join("+" + ln for ln in text.splitlines()))

    @staticmethod
    def private_vocab(public_text: str, hidden_text: str) -> set[str]:
        pub = hard_tokens(public_text, bare=True)
        return {t for t in hard_tokens(hidden_text) if t not in pub}

    @staticmethod
    def _hit(value: str, tok: str) -> bool:
        if re.search(r"[a-z]", tok, re.I):
            return re.search(r"(?<![A-Za-z0-9_])" + re.escape(tok) + r"(?![A-Za-z0-9_])", value, re.I) is not None
        return re.search(r"(?<![A-Za-z0-9_.])" + re.escape(tok) + r"(?![A-Za-z0-9_.])", value) is not None

    def audit(self, g: Gene, public_text: str = "", hidden_text: str = "", *, redact: bool = True) -> LeakageReport:
        vocab = self.private_vocab(public_text, hidden_text) if hidden_text else set()
        rep = LeakageReport()
        fields = ("summary", "signals_match", "strategy", "avoid", "preconditions", "validation")
        for f in fields:
            val = getattr(g, f)
            items = [val] if isinstance(val, str) else list(val)
            new = []
            for i, s in enumerate(items):
                for tok in vocab:
                    if self._hit(s, tok):
                        rep.leaks.append((tok, f"{f}[{i}]"))
                        if redact:
                            s = re.sub(re.escape(tok), "<redacted>", s, flags=re.I)
                            rep.redacted += 1
                new.append(s)
            if redact:
                setattr(g, f, new[0] if isinstance(val, str) else new)
        rep.critic_hits = self.deny_hits(g.text())
        return rep
