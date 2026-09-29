"""Memory system: canonical (per-label) + recency pool (axis E)."""
import json
import re
from collections import Counter, defaultdict
from typing import Any

CONFIG = {'cap': 6,
 'char_budget': 6000,
 'demo_chars': 0,
 'k': 16,
 'label_list': False,
 'learn': 'always',
 'lookup': {},
 'note_words': 5,
 'notes': False,
 'prompt': 'json',
 'select': 'topk',
 'store': 'all'}

WORD = re.compile(r"[a-z0-9]{3,}")


def _toks(text: str) -> set:
    return set(WORD.findall(text.lower().split("options:")[0]))


def _norm(s: Any) -> str:
    return str(s or "").strip().strip("`*\"'.,;[]{}() ").lower()


class Memory(MemorySystem):
    """Memory system: two-pool retrieval (canonical + recency)."""

    def __init__(self, llm):
        super().__init__(llm)
        self.canonical = {}         # {label: example} - one per label, first or most diverse
        self.recency = []           # recent examples, capped
        self.labels = []            # all labels seen
        self.confusions = Counter()
        self.words = defaultdict(Counter)
        self.recency_cap = 30       # keep at most 30 recent examples

    # ---------------------------------------------------------------- learning
    def learn_from_batch(self, batch_results):
        for r in batch_results:
            gold = _norm(r["ground_truth"])
            ok = bool(r.get("was_correct", True))
            if gold not in self.labels:
                self.labels.append(gold)
            if not ok:
                self.confusions[(_norm(r.get("prediction")), gold)] += 1
            self.words[gold].update(_toks(r["input"]))
            
            ex = {"input": r["input"], "target": gold, "hard": not ok}
            
            # Add to canonical if this label doesn't have one yet
            if gold not in self.canonical:
                self.canonical[gold] = ex
            
            # Add/update recency pool
            self.recency.append(ex)
            if len(self.recency) > self.recency_cap:
                self.recency.pop(0)

    # --------------------------------------------------------------- retrieval
    def _similar(self, query: str):
        q = _toks(query)
        scored = []
        # Score from recency pool only
        for i, e in enumerate(self.recency):
            t = _toks(e["input"])
            sim = len(q & t) / max(1, len(q | t))
            scored.append((sim, i, e))
        scored.sort(key=lambda x: (-x[0], -x[1]))
        return [e for _, _, e in scored]

    def _select(self, query: str):
        """Two-stage: canonical (all labels) + top-k similar from recency."""
        out, used = [], 0
        
        # Stage 1: Add canonical examples (one per label, ensures diversity)
        canonical_list = list(self.canonical.values())
        for e in canonical_list:
            n = len(e["input"]) + len(e["target"]) + 8
            if used + n > CONFIG["char_budget"]:
                break
            out.append(e)
            used += n
        
        # Stage 2: Add top-k similar from recency pool
        ranked = self._similar(query)
        for e in ranked:
            if e in out:  # Skip if already added from canonical
                continue
            n = len(e["input"]) + len(e["target"]) + 8
            if used + n > CONFIG["char_budget"]:
                break
            out.append(e)
            used += n
            if len([x for x in out if x not in canonical_list]) >= CONFIG["k"]:
                break
        
        # Reverse so most similar is closest to problem
        out.reverse()
        return out

    def _notes(self) -> str:
        if not self.words:
            return ""
        df = Counter()
        for c in self.words.values():
            df.update(set(c))
        lines = []
        for lab in self.labels:
            c = self.words.get(lab)
            if not c:
                continue
            best = sorted(c, key=lambda w: (-c[w] / df[w], -c[w], w))[:CONFIG["note_words"]]
            lines.append(f"- {lab}: {' '.join(best)}")
        return "\n".join(lines)

    # -------------------------------------------------------------- prediction
    def predict(self, input: str):
        for key, lab in CONFIG["lookup"].items():
            if key in input:
                return lab, {"lookup": True}
        parts = ["Classify the input into one of the labels, following the demonstrations."]
        if CONFIG["label_list"] and self.labels:
            parts.append("Labels: " + ", ".join(self.labels))
        if CONFIG["notes"]:
            notes = self._notes()
            if notes:
                parts.append("Label notes:\n" + notes)
        exs = self._select(input)
        if exs:
            dc = CONFIG["demo_chars"]
            parts.append("\n\n".join(f"Q: {e['input'][:dc] if dc else e['input']}\nA: {e['target']}"
                                         for e in exs))
        parts.append("**Problem:**\n" + input)
        if CONFIG["prompt"] == "json":
            parts.append('Respond in JSON: {"reasoning": "...", "final_answer": "<label>"}')
        else:
            parts.append("Reply with one line: Answer: <label>")
        response = self.call_llm("\n\n".join(parts))
        answer = extract_json_field(response, "final_answer")
        return answer, {"n_examples": len(exs)}

    def get_state(self) -> str:
        return json.dumps({
            "canonical": list(self.canonical.values()),
            "recency": self.recency,
            "labels": self.labels,
            "confusions": [[p, g, n] for (p, g), n in self.confusions.items()],
            "words": {k: dict(v) for k, v in self.words.items()}
        })

    def set_state(self, state: str) -> None:
        d = json.loads(state or "{}")
        self.labels = d.get("labels", [])
        self.recency = d.get("recency", [])
        self.confusions = Counter({(p, g): n for p, g, n in d.get("confusions", [])})
        self.words = defaultdict(Counter, {k: Counter(v) for k, v in d.get("words", {}).items()})
        # Rebuild canonical from persisted list
        self.canonical = {}
        for ex in d.get("canonical", []):
            if "target" in ex:
                self.canonical[ex["target"]] = ex
