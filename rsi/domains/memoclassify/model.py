"""MemoLM: the frozen, deterministic "base model" of MemoClassify.

It behaves like a small instruction-following LLM whose only skills are
in-context learning and a little prior knowledge, so the *harness* (what the
memory system stores, retrieves and shows) decides accuracy:

* it reads labelled demonstrations from the prompt in the usual formats
  (``Q: ... / A: ...``, ``Input: ... / Label: ...``, ``Text: ... / Output: ...``);
* it reads label lists (``Labels: a, b, c`` / ``Options: a; b``) and label notes
  (``- <label>: word word`` or ``<label> => word word``);
* it answers with the label whose demonstrations are lexically most similar to
  the query (nearest neighbour + top-2 mean), plus weak prior knowledge of some
  keywords (hash-based) - but it can only answer labels that appear in the prompt;
* past ``budget_chars`` it degrades: demonstrations in the middle of a long prompt
  are ignored ("lost in the middle") and random errors become more likely;
* it answers in JSON ``{"reasoning", "final_answer"}`` when the prompt mentions
  ``final_answer``, else ``Answer: <label>``.

``variant="B"`` is a different "model" for transfer tests: other prior knowledge,
other tie-breaking, a smaller context budget and more noise.
"""
from __future__ import annotations

import hashlib
import json
import random
import re
import threading
from typing import Iterable, Optional

from ...core.llm import LLM, LLMResponse, Usage, estimate_tokens
from .data import ClassDataset

_TOK = re.compile(r"[a-z0-9]{3,}")
_PAIR = re.compile(r"^[ \t>*-]*(?:Q|Input|Text|Question|Example|Query)\s*[:=]\s*(?P<q>.+?)\s*\n"
                   r"[ \t>*-]*(?:A|Label|Answer|Output|Category|Class)\s*[:=]\s*(?P<a>[^\n]+)", re.M | re.I)
_LABELS = re.compile(r"^[ \t>*-]*(?:labels|allowed labels|possible labels|valid labels|categories|label set|"
                     r"options|classes)\s*:\s*(?P<body>.*)$", re.M | re.I)
_NOTE = re.compile(r"^[ \t]*(?:[-*]\s*)?(?P<label>[a-z]{2,}-[a-z]{2,})\s*(?::|=>|->)\s*(?P<words>[^\n]+)$", re.M | re.I)

VARIANTS = {
    # q_know: share of label keywords the model "knows"; eps: base noise
    "A": {"q_know": 0.4, "eps": 0.03, "budget": 12000, "tie": "first"},
    "B": {"q_know": 0.3, "eps": 0.06, "budget": 9000, "tie": "last"},
}


def tokens(text: str) -> frozenset[str]:
    return frozenset(_TOK.findall(text.lower()))


def _rng(*parts) -> random.Random:
    return random.Random(int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:16], 16))


def _jacc(a: frozenset, b: frozenset) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


class MemoLM(LLM):
    """Deterministic simulated classifier LLM (see module docstring)."""

    def __init__(self, datasets: Iterable[ClassDataset], variant: str = "A", *, budget_chars: Optional[int] = None,
                 name: Optional[str] = None) -> None:
        super().__init__()
        self.variant = variant
        p = VARIANTS[variant]
        self.q_know = p["q_know"]
        self.eps = p["eps"]
        self.budget = budget_chars or p["budget"]
        self.tie = p["tie"]
        self.name = name or f"memolm-{variant}"
        self.inputs: dict[str, str] = {}
        self.labels: set[str] = set()
        self.knowledge: dict[str, str] = {}      # keyword -> label the model associates it with
        for ds in datasets:
            self.labels.update(ds.labels)
            for part in (ds.train, ds.val, ds.test):
                for ex in part:
                    self.inputs[ex.text] = ds.name
            for lab, kws in ds.keywords.items():
                for w in kws:
                    if _rng("know", variant, w).random() < self.q_know:
                        self.knowledge[w] = lab
        self._tok_cache: dict[str, frozenset] = {}
        self._lock = threading.Lock()
        self.n_calls = 0

    # ------------------------------------------------------------------ parsing
    def _tok(self, s: str) -> frozenset:
        t = self._tok_cache.get(s)
        if t is None:
            t = tokens(s)
            if len(self._tok_cache) < 200000:
                self._tok_cache[s] = t
        return t

    def _norm_label(self, s: str) -> str:
        return s.strip().strip("`*\"'.,;[]{}() ").lower()

    def parse(self, prompt: str) -> dict:
        examples = []   # (start, end, question, label)
        spans = []
        for m in _PAIR.finditer(prompt):
            lab = self._norm_label(m.group("a"))
            if lab in self.labels:
                examples.append((m.start(), m.end(), m.group("q"), lab))
                spans.append((m.start(), m.end()))
        label_list: set[str] = set()
        for m in _LABELS.finditer(prompt):
            for part in re.split(r"[;,|]", m.group("body")):
                lab = self._norm_label(part)
                if lab in self.labels:
                    label_list.add(lab)
        notes: dict[str, set[str]] = {}
        for m in _NOTE.finditer(prompt):
            lab = self._norm_label(m.group("label"))
            if lab in self.labels:
                notes.setdefault(lab, set()).update(self._tok(m.group("words")))
                label_list.add(lab)
        query = self._find_query(prompt, spans)
        return {"examples": examples, "labels": label_list, "notes": notes, "query": query}

    def _find_query(self, prompt: str, spans: list[tuple[int, int]]) -> Optional[str]:
        def inside(pos: int) -> bool:
            return any(a <= pos < b for a, b in spans)

        best: tuple[int, Optional[str]] = (-1, None)
        pos = 0
        for line in prompt.splitlines(keepends=True):
            s = line.strip()
            cands = [s]
            if ":" in s:
                cands.append(s.split(":", 1)[1].strip())
            cands.append(s.strip("*`\"' "))
            for c in cands:
                if c in self.inputs and not inside(pos) and pos > best[0]:
                    best = (pos, c)
            pos += len(line)
        if best[1] is not None:
            return best[1]
        # slow path: substring search (inputs embedded mid-line)
        for inp in self.inputs:
            i = prompt.rfind(inp)
            if i >= 0 and not inside(i) and i > best[0]:
                best = (i, inp)
        return best[1]

    # --------------------------------------------------------------- predicting
    def classify(self, prompt: str, seed: Optional[int] = None) -> tuple[Optional[str], dict]:
        info = self.parse(prompt)
        q = info["query"]
        n = len(prompt)
        overflow = max(0.0, (n - self.budget) / self.budget)
        examples = info["examples"]
        if overflow > 0:
            half = self.budget / 2
            examples = [e for e in examples if e[0] < half or e[1] > n - half]
        cand: dict[str, list[float]] = {}
        qt = self._tok(q) if q else frozenset()
        opts: set[str] = set()
        if q and "options:" in q.lower():
            body = q.lower().split("options:", 1)[1]
            opts = {self._norm_label(x) for x in re.split(r"[;,|]", body)} & self.labels
            qt = self._tok(q.lower().split("options:", 1)[0])
        for _, _, eq, lab in examples:
            et = self._tok(eq.lower().split("options:", 1)[0])
            cand.setdefault(lab, []).append(_jacc(qt, et))
        labels = set(cand) | info["labels"] | opts
        diag = {"n_examples": len(info["examples"]), "n_visible": len(examples), "n_labels": len(labels),
                "overflow": round(overflow, 3), "query_found": q is not None}
        if not labels or q is None:
            return None, diag
        scores = {}
        for lab in labels:
            sims = sorted(cand.get(lab, []), reverse=True)
            nn = sims[0] if sims else 0.0
            knn = sum(sims[:2]) / 2 if len(sims) >= 2 else nn * 0.5
            kw = 1.0 if any(self.knowledge.get(w) == lab for w in qt) else 0.0
            note = len(qt & info["notes"].get(lab, frozenset())) / max(1, len(qt))
            scores[lab] = nn + 0.5 * knn + 0.35 * kw + 1.5 * note
        rng = _rng("noise", self.variant, prompt, seed)
        order = sorted(scores, key=lambda l: (-scores[l], l if self.tie == "first" else "".join(reversed(l))))
        ans = order[0]
        if rng.random() < self.eps + 0.25 * min(1.0, overflow):
            ans = rng.choice(sorted(labels))
        return ans, diag

    def _complete(self, prompt, *, system, max_tokens, seed) -> LLMResponse:
        full = (system or "") + "\n" + prompt
        with self._lock:
            self.n_calls += 1
        ans, diag = self.classify(full, seed)
        if ans is None:
            q = self.parse(full)["query"] or ""
            words = sorted(self._tok(q)) or ["unknown"]
            ans = _rng("guess", prompt).choice(words)
        low = full.lower()
        if "final_answer" in low:
            text = json.dumps({"reasoning": f"Closest demonstrations suggest {ans}.", "final_answer": ans})
        elif "answer:" in low:
            text = f"Reasoning: matched demonstrations.\nAnswer: {ans}"
        else:
            text = ans
        usage = Usage(1, estimate_tokens(full), estimate_tokens(text), 0.0, 0.0)
        return LLMResponse(text=text, usage=usage, model=self.name, raw={"diag": diag})
