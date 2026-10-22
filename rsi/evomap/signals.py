"""Signals: extraction layers, pattern matching, de-duplication and plateau detection (spec §3.2, §4.2, §4.6)."""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache
from itertools import takewhile
from typing import Callable, Iterable, Optional, Sequence

MAX_REGEX_PATTERN_LEN = 1024
STOPWORDS = {"the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "is", "are", "be", "by", "at",
             "as", "it", "this", "that", "from", "if", "not", "no", "but", "into"}

PROBLEM_SIGNALS = ("log_error", "recurring_error", "capability_gap", "perf_bottleneck", "test_failure",
                   "deployment_issue")
OPPORTUNITY_SIGNALS = ("user_feature_request", "user_improvement_suggestion", "perf_bottleneck", "capability_gap",
                       "stable_success_plateau", "external_opportunity", "recurring_error", "unsupported_input_type",
                       "evolution_stagnation_detected", "repair_loop_detected", "force_innovation_after_repair_loop",
                       "tool_bypass", "curriculum_target", "issue_already_resolved", "openclaw_self_healed",
                       "empty_cycle_loop_detected")
ERROR_SIGNALS = ("log_error", "error", "exception", "failed", "unstable")
ADAPTER_TOKENS = {"task"}  # the task adapter's "task:<family>" prefix carries no meaning


def _regex(p: str) -> Optional[re.Pattern]:
    m = re.fullmatch(r"/(.+)/([a-z]*)", p, re.S)
    if not m or len(p) > MAX_REGEX_PATTERN_LEN:
        return None
    flags = 0
    for ch in (m.group(2) or "i"):
        flags |= {"i": re.I, "m": re.M, "s": re.S}.get(ch, 0)
    try:
        return re.compile(m.group(1), flags)
    except re.error:
        return None


@lru_cache(maxsize=200_000)
def _prep(pattern: str):
    p = str(pattern or "")
    if p.startswith("/") and p.count("/") >= 2:
        rx = _regex(p)
        if rx is not None:
            return ("re", rx)
    branches = [b.strip().lower() for b in p.split("|")] if "|" in p else [p.lower()]
    return ("sub", tuple(b for b in branches if b))


def _match(prep, signals: Sequence[str], low: Sequence[str]) -> bool:
    kind, obj = prep
    if kind == "re":
        return any(obj.search(s) for s in signals)
    return any(b in s for b in obj for s in low)


def match_pattern(pattern: str, signals: Sequence[str]) -> bool:
    """``/body/flags`` regex, ``a|b`` alias list, else case-insensitive substring."""
    return _match(_prep(pattern), signals, [s.lower() for s in signals])


def pattern_hits(patterns: Iterable[str], signals: Sequence[str]) -> int:
    low = [s.lower() for s in signals]
    return sum(1 for p in patterns if _match(_prep(p), signals, low))


def tokens(text: str) -> list[str]:
    """Lowercase tokens of length >= 2, without stopwords and adapter tokens."""
    return [t for t in re.split(r"[^a-z0-9_\-]+", str(text).lower())
            if len(t) >= 2 and t not in STOPWORDS and t not in ADAPTER_TOKENS]


def cosine(a: Sequence[str], b: Sequence[str]) -> float:
    ca, cb = Counter(a), Counter(b)
    if not ca or not cb:
        return 0.0
    dot = sum(v * cb.get(k, 0) for k, v in ca.items())
    na = math.sqrt(sum(v * v for v in ca.values()))
    nb = math.sqrt(sum(v * v for v in cb.values()))
    return dot / (na * nb) if na and nb else 0.0


def jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 1.0
    return len(sa & sb) / len(sa | sb) if (sa | sb) else 0.0


def trailing(seq: Sequence, pred: Callable) -> int:
    """Length of the run at the end of ``seq`` where ``pred`` holds."""
    return len(list(takewhile(pred, reversed(seq))))


_TAG_RULES = [
    (r"error|exception|fail|crash|unstable|errsig", ["problem:reliability", "action:repair"]),
    (r"protocol", ["problem:protocol", "area:prompt"]),
    (r"perf|slow|latency|timeout|bottleneck|token", ["problem:performance"]),
    (r"capability|unsupported|feature|missing", ["problem:capability", "action:innovate"]),
    (r"stagnation|plateau|saturation|empty_cycle", ["problem:stagnation", "action:innovate"]),
    (r"orchestrat|agent|subagent", ["area:orchestration"]),
    (r"memory|context", ["area:memory"]),
    (r"skill|gene", ["area:skills"]),
    (r"validat|test", ["risk:validation"]),
]


def expand_signals(signals: Iterable[str]) -> set[str]:
    """Signals plus their ``prefix:`` heads and regex-derived tags (Evolver's learningSignals)."""
    out: set[str] = set()
    for s in signals:
        s = str(s).lower()
        out.add(s)
        if ":" in s:
            out.add(s.split(":", 1)[0])
        for rx, tags in _TAG_RULES:
            if re.search(rx, s):
                out.update(tags)
    return out


def signal_key(signals: Iterable[str]) -> str:
    return "|".join(sorted({str(s) for s in signals}))


def is_problem(signals: Sequence[str]) -> bool:
    return any(s in PROBLEM_SIGNALS or s.startswith("errsig:") for s in signals)


def has_error(signals: Sequence[str]) -> bool:
    return any(s in ERROR_SIGNALS or s.startswith(("errsig:", "errsig_norm:")) for s in signals)


@dataclass
class RunContext:
    """What an extractor may read: public task info and the agent's own logs (never the target)."""

    task: object = None
    corpus: str = ""
    tool_uses: Counter = field(default_factory=Counter)


class RegexSignalExtractor:
    """Layer 1: deterministic regexes over the corpus (§3.2)."""

    ERR = re.compile(r"\[error|error:|exception:|iserror\"\s*:\s*true|\"status\"\s*:\s*\"(?:error|failed)\"|traceback",
                     re.I)
    SIG = re.compile(r"^.*(?:TypeError:|ReferenceError:|SyntaxError:|AssertionError|NameError:|ValueError:|"
                     r"KeyError:|IndexError:|error:|exception:|\[error).*$", re.I | re.M)
    PERF = re.compile(r"slow|timeout|timed out|latency|bottleneck|took too long|high cpu|high memory|\boom\b|"
                      r"out of memory", re.I)
    GAP = re.compile(r"not supported|cannot|doesn't support|no way to|missing feature|unsupported|not available|"
                     r"not implemented", re.I)
    FEATURE = re.compile(r"please add|feature request|would be nice|can you add|support for", re.I)

    def extract_text(self, corpus: str, tool_uses: Optional[Counter] = None) -> list[str]:
        out: list[str] = []
        text = corpus or ""
        if self.ERR.search(text):
            out.append("log_error")
            m = self.SIG.search(text)
            if m:
                out.append("errsig:" + m.group(0).strip()[:260])
            if len(self.ERR.findall(text)) >= 3:
                out.append("recurring_error")
        for rx, sig in ((self.PERF, "perf_bottleneck"), (self.GAP, "capability_gap"),
                        (self.FEATURE, "user_feature_request")):
            if rx.search(text):
                out.append(sig)
        out += [f"high_tool_usage:{tool}" for tool, n in (tool_uses or {}).items() if n >= 10]
        return out

    def extract(self, ctx: RunContext) -> list[str]:
        return self.extract_text(ctx.corpus, ctx.tool_uses)


class KeywordSignalScorer:
    """Layer 2: a signal fires when sum(count(kw) * weight) >= threshold (§3.2)."""

    DEFAULT_PROFILES = {
        "perf_bottleneck": ({"slow": 2, "latency": 2, "timeout": 3, "tokens": 1, "expensive": 2}, 6),
        "capability_gap": ({"unsupported": 3, "cannot": 2, "missing": 2, "not implemented": 3}, 5),
        "recurring_error": ({"error": 2, "failed": 2, "again": 1, "still": 1}, 7),
    }

    def __init__(self, profiles: Optional[dict] = None) -> None:
        self.profiles = profiles if profiles is not None else dict(self.DEFAULT_PROFILES)

    def score(self, text: str) -> list[str]:
        low = (text or "").lower()
        return [sig for sig, (kws, thr) in self.profiles.items()
                if sum(low.count(k) * w for k, w in kws.items()) >= thr]


class TaskSignalExtractor:
    """Task-stream adapter: ``task:<family>``, public meta signals/keywords, optional input keywords, layers 1-2."""

    def __init__(self, *, family_prefix: str = "task:", input_keywords: int = 0,
                 keyword_scorer: Optional[KeywordSignalScorer] = None) -> None:
        self.family_prefix = family_prefix
        self.input_keywords = input_keywords
        self.regex = RegexSignalExtractor()
        self.kw = keyword_scorer or KeywordSignalScorer()

    def task_signals(self, task) -> list[str]:
        if task is None:
            return []
        out = []
        fam = getattr(task, "family", None)
        if fam:
            out.append(f"{self.family_prefix}{fam}")
        meta = getattr(task, "meta", {}) or {}
        out.extend(str(s) for s in meta.get("signals", []))
        out.extend(str(s) for s in meta.get("keywords", []))
        if self.input_keywords:
            inp = task.input if isinstance(task.input, str) else str(task.input)
            toks = [t for t in tokens(inp) if not t.isdigit() and len(t) > 3]
            out.extend(sorted(set(toks), key=toks.index)[: self.input_keywords])
        return out

    def extract(self, ctx: RunContext) -> list[str]:
        sig = self.task_signals(ctx.task) + self.regex.extract(ctx) + self.kw.score(ctx.corpus)
        return list(dict.fromkeys(sig))


class HubSignalLayer:
    """Layer 3 (Evolver ``signals.js:_extractLLM``): on calls 1, 1+interval, ... send the corpus to the hub analyzer.

    Any error, missing analyzer or malformed reply yields ``[]``; the counter advances on every call."""

    def __init__(self, analyzer=None, *, sender_id: Optional[str] = None, interval: int = 5,
                 max_chars: int = 2000, max_signals: int = 10) -> None:
        self.analyzer = analyzer
        self.sender_id = sender_id
        self.interval = interval
        self.max_chars = max_chars
        self.max_signals = max_signals
        self.count = 0
        self.calls: list[dict] = []

    def extract(self, corpus: str) -> list[str]:
        self.count += 1
        if self.count % self.interval != 1 or self.analyzer is None:
            return []
        payload = {"corpus_summary": (corpus or "")[: self.max_chars], "signal_types": list(OPPORTUNITY_SIGNALS),
                   "sender_id": self.sender_id}
        try:
            reply = self.analyzer(payload)
        except Exception:
            reply = None
        sig = reply.get("signals") if isinstance(reply, dict) else None
        out = [x for x in sig if isinstance(x, str) and 0 < len(x) < 200][: self.max_signals] \
            if isinstance(sig, list) else []
        self.calls.append({"count": self.count, "payload": payload, "signals": out})
        return out


def merge_signals(regex_and_score: Sequence[str], llm: Sequence[str]) -> list[str]:
    """Evolver ``_mergeSignals``: the ordered union of the layers."""
    return list(dict.fromkeys([*regex_and_score, *llm]))


def _collapse(s: str) -> str:
    for pre in ("errsig:", "recurring_errsig", "user_feature_request:", "user_improvement_suggestion:"):
        if s.startswith(pre):
            return pre.rstrip(":")
    return s


def _empty(e: dict) -> bool:
    br = e.get("blast_radius") or {}
    return not br.get("files") and not br.get("lines")


def _status(e: dict):
    return (e.get("outcome") or {}).get("status")


@dataclass
class DedupResult:
    signals: list[str]
    suppressed: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    ban_gene: Optional[str] = None


class SignalDeduper:
    """§4.6 control rules over recent events; task-descriptor signals (``protected_prefixes``) are never suppressed."""

    def __init__(self, *, window: int = 8, suppress_min: int = 3, protected_prefixes: Sequence[str] = ("task:",),
                 protected: Sequence[str] = ()) -> None:
        self.window = window
        self.suppress_min = suppress_min
        self.protected_prefixes = tuple(protected_prefixes)
        self.protected = set(protected)

    def _protected(self, s: str) -> bool:
        return s in self.protected or s.startswith(self.protected_prefixes)

    def _free(self, signals: Sequence[str]) -> list[str]:
        return [s for s in signals if not self._protected(s)]

    def apply(self, signals: Sequence[str], recent_events: Sequence) -> DedupResult:
        ev = [e.to_dict() if hasattr(e, "to_dict") else e for e in recent_events]
        last = ev[-self.window:]
        S = list(dict.fromkeys(signals))
        res = DedupResult(S)
        counts: Counter = Counter()
        for e in last:
            counts.update({_collapse(s) for s in e.get("signals", [])})
        suppressed = [s for s in S if not self._protected(s) and counts[_collapse(s)] >= self.suppress_min]
        S2 = [s for s in S if s not in suppressed]
        if suppressed and not self._free(S2) and self._free(S):
            S2 += ["evolution_stagnation_detected", "stable_success_plateau"]
        res.suppressed = suppressed
        hist = ev[-10:]
        repairs = trailing(hist, lambda e: e.get("intent") == "repair")
        if repairs >= 3:
            S2 = [s for s in S2 if not (s == "log_error" or s.startswith(("errsig:", "recurring_errsig")))]
            if not self._free(S2):
                S2 += ["repair_loop_detected", "stable_success_plateau"]
            S2.append("force_innovation_after_repair_loop")
        if sum(1 for e in last if _empty(e)) >= 4:
            S2 = [s for s in S2 if not (s == "log_error" or s.startswith("errsig:"))]
            S2 += ["empty_cycle_loop_detected", "stable_success_plateau"]
        cons_empty = trailing(hist, _empty)
        if cons_empty >= 5:
            S2 += ["force_steady_state", "evolution_saturation"]
        elif cons_empty >= 3:
            S2.append("evolution_saturation")
        if cons_empty >= 3:
            S2.append("explore_opportunity")
        fails = trailing(hist, lambda e: not _empty(e) and _status(e) == "failed")  # an empty cycle breaks it
        if fails >= 3:
            S2.append(f"consecutive_failure_streak_{fails}")
            if fails >= 5:
                S2.append("failure_loop_detected")
                genes = Counter(g for e in last for g in e.get("genes_used", []))
                if genes:
                    gid = genes.most_common(1)[0][0]
                    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_\-]{0,127}", gid):
                        S2.append(f"ban_gene:{gid}")
                        res.ban_gene = gid
        nonempty = [e for e in last if not _empty(e)]
        if len(last) >= 4 and nonempty:
            if sum(1 for e in nonempty if _status(e) == "failed") / len(last) >= 0.75:
                S2 += ["high_failure_ratio", "force_innovation_after_repair_loop"]
        if len(ev) >= 4:
            sc = [float((e.get("outcome") or {}).get("score", 0.0)) for e in ev[-6:]]
            prev = [float((e.get("outcome") or {}).get("score", 0.0)) for e in ev[-12:-6]]
            avg = sum(sc) / len(sc)
            improving = bool(prev) and avg > sum(prev) / len(prev) + 0.05
            if avg < 0.35 and not improving:
                S2.append("plateau_pivot_required")
            elif avg < 0.55 and not improving and repairs >= 2:
                S2.append("plateau_pivot_suggested")
        res.signals = list(dict.fromkeys(S2 or ["stable_success_plateau"]))
        return res


@dataclass
class PlateauOverride:
    active: bool
    severity: str = "none"      # suggested | required


class PlateauDetector:
    """§3.1: k trailing non-success outcomes among the last 10 events; k >= 10 required, k >= 5 suggested."""

    def __init__(self, suggested: int = 5, required: int = 10) -> None:
        self.suggested, self.required = suggested, required

    def override(self, recent_events: Sequence, hub_directive: Optional[str] = None) -> PlateauOverride:
        if hub_directive == "pivot_check":
            return PlateauOverride(True, "suggested")
        ev = [e.to_dict() if hasattr(e, "to_dict") else e for e in recent_events][-10:]
        k = trailing(ev, lambda e: _status(e) != "success")
        if k >= self.required:
            return PlateauOverride(True, "required")
        if k >= self.suggested:
            return PlateauOverride(True, "suggested")
        return PlateauOverride(False)
