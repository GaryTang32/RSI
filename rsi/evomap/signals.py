"""Signals: extraction, pattern matching and de-duplication (spec §3.2, §4.2, §4.6).

A *signal* is a short string describing the situation (``log_error``,
``errsig:TypeError: ...``, ``perf_bottleneck``, ``task:unicode`` ...). Genes
declare ``signals_match`` patterns; the selector scores genes by how many of
their patterns match the current signals.

Components

* :func:`match_pattern` - substring / ``/regex/flags`` / ``a|b|c`` aliases;
* :func:`expand_signals` - adds ``prefix`` and tag signals (``problem:*``,
  ``area:*``, ``action:*``) used by the current engine's tag score;
* :class:`RegexSignalExtractor` - layer 1 (deterministic regexes over a text corpus);
* :class:`KeywordSignalScorer` - layer 2 (weighted keyword profiles, thresholds);
* :class:`TaskSignalExtractor` - the domain adapter for a task stream: task
  family/meta signals + layers 1-2 over the previous trial's trace;
* :class:`SignalDeduper` - the §4.6 control rules (suppression, repair loops,
  empty cycles, failure streaks + ``ban_gene:<id>``, plateau pivots).
"""
from __future__ import annotations

import math
import re
from functools import lru_cache
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable, Optional, Protocol, Sequence

MAX_REGEX_PATTERN_LEN = 1024
STOPWORDS = {"the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "is", "are", "be", "by", "at",
             "as", "it", "this", "that", "from", "if", "not", "no", "but", "into"}   # 26 English stopwords

PROBLEM_SIGNALS = ("log_error", "recurring_error", "capability_gap", "perf_bottleneck", "test_failure",
                   "deployment_issue")
OPPORTUNITY_SIGNALS = ("user_feature_request", "user_improvement_suggestion", "perf_bottleneck", "capability_gap",
                       "stable_success_plateau", "external_opportunity", "recurring_error", "unsupported_input_type",
                       "evolution_stagnation_detected", "repair_loop_detected", "force_innovation_after_repair_loop",
                       "tool_bypass", "curriculum_target", "issue_already_resolved", "openclaw_self_healed",
                       "empty_cycle_loop_detected")
ERROR_SIGNALS = ("log_error", "error", "exception", "failed", "unstable")


# ----------------------------------------------------------------------------- matching
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
    """MATCH(p, signals): ``/body/flags`` regex, ``a|b`` alias list, else case-insensitive substring."""
    return _match(_prep(pattern), signals, [s.lower() for s in signals])


def pattern_hits(patterns: Iterable[str], signals: Sequence[str]) -> int:
    low = [s.lower() for s in signals]
    return sum(1 for p in patterns if _match(_prep(p), signals, low))


#: the task-stream adapter's own prefix ("task:<family>") carries no meaning for semantic similarity
ADAPTER_TOKENS = {"task"}


def tokens(text: str) -> list[str]:
    """Lowercase tokens split on non letter/digit/_/-, length >= 2, stopwords (and the adapter's
    ``task`` prefix) removed."""
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
    """Signals + their pre-':' prefixes + regex-derived tags (current engine's learningSignals)."""
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


# ----------------------------------------------------------------------------- extraction
class SignalExtractor(Protocol):
    def extract(self, ctx: "RunContext") -> list[str]: ...


@dataclass
class RunContext:
    """What the extractor may read: public task info plus the agent's own logs."""

    task: object = None                   # rsi.core Task (the extractor must not read its target)
    corpus: str = ""                      # logs / previous trace / public check output
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
            n_err = len(self.ERR.findall(text))
            if n_err >= 3:
                out.append("recurring_error")
        if self.PERF.search(text):
            out.append("perf_bottleneck")
        if self.GAP.search(text):
            out.append("capability_gap")
        if self.FEATURE.search(text):
            out.append("user_feature_request")
        for tool, n in (tool_uses or {}).items():
            if n >= 10:
                out.append(f"high_tool_usage:{tool}")
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
        out = []
        for sig, (kws, thr) in self.profiles.items():
            s = sum(low.count(k) * w for k, w in kws.items())
            if s >= thr:
                out.append(sig)
        return out


class TaskSignalExtractor:
    """Domain adapter for a stream of rsi.core tasks.

    Signals = ``task:<family>`` + the task's public ``meta["signals"]`` (and
    ``meta["keywords"]``) + optional keywords from the task input text + layers
    1-2 over the corpus (the previous attempt's trace / public check output).
    Never reads ``task.target``.
    """

    def __init__(self, *, family_prefix: str = "task:", input_keywords: int = 0,
                 keyword_scorer: Optional[KeywordSignalScorer] = None) -> None:
        self.family_prefix = family_prefix
        self.input_keywords = input_keywords
        self.regex = RegexSignalExtractor()
        self.kw = keyword_scorer or KeywordSignalScorer()

    def task_signals(self, task) -> list[str]:
        out = []
        if task is None:
            return out
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
        sig = self.task_signals(ctx.task)
        sig += self.regex.extract(ctx)
        sig += self.kw.score(ctx.corpus)
        return list(dict.fromkeys(sig))


# ----------------------------------------------------------------------------- de-duplication
def _collapse(s: str) -> str:
    for pre in ("errsig:", "recurring_errsig", "user_feature_request:", "user_improvement_suggestion:"):
        if s.startswith(pre):
            return pre.rstrip(":")
    return s


@dataclass
class DedupResult:
    signals: list[str]
    suppressed: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    ban_gene: Optional[str] = None


class SignalDeduper:
    """§4.6 control rules over the last events.

    ``protected_prefixes``: task-descriptor signals (added by a task-stream
    adapter) are never suppressed - in Evolver all signals come from logs, where
    a repeated signal means stagnation; a repeated *task class* does not.
    """

    def __init__(self, *, window: int = 8, suppress_min: int = 3, protected_prefixes: Sequence[str] = ("task:",),
                 protected: Sequence[str] = ()) -> None:
        self.window = window
        self.suppress_min = suppress_min
        self.protected_prefixes = tuple(protected_prefixes)
        self.protected = set(protected)

    def _protected(self, s: str) -> bool:
        return s in self.protected or s.startswith(self.protected_prefixes)

    def apply(self, signals: Sequence[str], recent_events: Sequence) -> DedupResult:
        ev = [e.to_dict() if hasattr(e, "to_dict") else e for e in recent_events]
        last = ev[-self.window:]
        S = list(dict.fromkeys(signals))
        res = DedupResult(S)
        # suppression: key appears in >= 3 of the last 8 events
        counts: Counter = Counter()
        for e in last:
            counts.update({_collapse(s) for s in e.get("signals", [])})
        suppressed = [s for s in S if not self._protected(s) and counts[_collapse(s)] >= self.suppress_min]
        S2 = [s for s in S if s not in suppressed]
        if suppressed and not [s for s in S2 if not self._protected(s)] and [s for s in S if not self._protected(s)]:
            S2 += ["evolution_stagnation_detected", "stable_success_plateau"]
        res.suppressed = suppressed
        # history analysis (last 10 / last 8)
        hist = ev[-10:]

        def trailing(pred) -> int:
            n = 0
            for e in reversed(hist):
                if pred(e):
                    n += 1
                else:
                    break
            return n

        def is_empty(e) -> bool:
            br = e.get("blast_radius") or {}
            return not br.get("files") and not br.get("lines")

        repairs = trailing(lambda e: e.get("intent") == "repair")
        if repairs >= 3:
            S2 = [s for s in S2 if not (s == "log_error" or s.startswith(("errsig:", "recurring_errsig")))]
            if not [s for s in S2 if not self._protected(s)]:
                S2 += ["repair_loop_detected", "stable_success_plateau"]
            S2.append("force_innovation_after_repair_loop")
        n_empty = sum(1 for e in last if is_empty(e))
        if n_empty >= 4:
            S2 = [s for s in S2 if not (s == "log_error" or s.startswith("errsig:"))]
            S2 += ["empty_cycle_loop_detected", "stable_success_plateau"]
        cons_empty = trailing(is_empty)
        if cons_empty >= 5:
            S2 += ["force_steady_state", "evolution_saturation"]
        elif cons_empty >= 3:
            S2.append("evolution_saturation")
        if cons_empty >= 3:
            S2.append("explore_opportunity")
        # consecutive non-empty failures (an empty cycle breaks the streak)
        fails = 0
        for e in reversed(hist):
            if is_empty(e):
                break
            if (e.get("outcome") or {}).get("status") == "failed":
                fails += 1
            else:
                break
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
        nonempty = [e for e in last if not is_empty(e)]
        if len(last) >= 4 and nonempty:
            ratio = sum(1 for e in nonempty if (e.get("outcome") or {}).get("status") == "failed") / len(last)
            if ratio >= 0.75:
                S2 += ["high_failure_ratio", "force_innovation_after_repair_loop"]
        # plateau pivots from scores
        if len(ev) >= 4:
            sc = [float((e.get("outcome") or {}).get("score", 0.0)) for e in ev[-6:]]
            prev = [float((e.get("outcome") or {}).get("score", 0.0)) for e in ev[-12:-6]]
            avg = sum(sc) / len(sc)
            improving = bool(prev) and avg > sum(prev) / len(prev) + 0.05
            if avg < 0.35 and not improving:
                S2.append("plateau_pivot_required")
            elif avg < 0.55 and not improving and repairs >= 2:
                S2.append("plateau_pivot_suggested")
        if not S2:
            S2 = ["stable_success_plateau"]
        res.signals = list(dict.fromkeys(S2))
        return res


@dataclass
class PlateauOverride:
    active: bool
    severity: str = "none"      # suggested | required


class PlateauDetector:
    """Enrich-stage plateau detector (§3.1): k consecutive non-success outcomes among
    the last 10 events; k >= 10 -> required (+ drift), k >= 5 -> suggested."""

    def __init__(self, suggested: int = 5, required: int = 10) -> None:
        self.suggested, self.required = suggested, required

    def override(self, recent_events: Sequence, hub_directive: Optional[str] = None) -> PlateauOverride:
        if hub_directive == "pivot_check":
            return PlateauOverride(True, "suggested")
        ev = [e.to_dict() if hasattr(e, "to_dict") else e for e in recent_events][-10:]
        k = 0
        for e in reversed(ev):
            if (e.get("outcome") or {}).get("status") != "success":
                k += 1
            else:
                break
        if k >= self.required:
            return PlateauOverride(True, "required")
        if k >= self.suggested:
            return PlateauOverride(True, "suggested")
        return PlateauOverride(False)
