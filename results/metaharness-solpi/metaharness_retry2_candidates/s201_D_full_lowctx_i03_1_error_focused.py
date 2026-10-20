"""Memory system: error-focused learning with adaptive rival selection."""
import json
import re
from collections import Counter, defaultdict, deque
from typing import Any

CONFIG = {'cap': 6,
 'char_budget': 7000,
 'demo_chars': 0,
 'k': 12,
 'label_list': False,
 'learn': 'always',
 'lookup': {},
 'note_words': 5,
 'notes': False,
 'prompt': 'json',
 'select': 'adaptive_error',
 'store': 'error_focused'}

WORD = re.compile(r"[a-z0-9]{3,}")


def _toks(text: str) -> set:
    return set(WORD.findall(text.lower().split("options:")[0]))


def _norm(s: Any) -> str:
    return str(s or "").strip().strip("`*\"'.,;[]{}() ").lower()


class Memory(MemorySystem):
    """Memory system: error-focused learning with adaptive rival selection."""

    def __init__(self, llm):
        super().__init__(llm)
        self.examples = []          # {"input", "target", "hard"}
        self.labels = []            # labels in order of first appearance
        self.confusions = Counter() # (predicted, gold) -> count
        self.words = defaultdict(Counter)
        self.recent_errors = deque(maxlen=30)  # Track recent (pred, gold) pairs for hard cases

    # ---------------------------------------------------------------- learning
    def learn_from_batch(self, batch_results):
        for r in batch_results:
            gold = _norm(r["ground_truth"])
            ok = bool(r.get("was_correct", True))
            pred = _norm(r.get("prediction", ""))
            
            if gold not in self.labels:
                self.labels.append(gold)
            
            # Track hard cases (errors) for adaptive selection
            if not ok:
                self.confusions[(pred, gold)] += 1
                self.recent_errors.append((pred, gold))
            
            # Track per-label keywords
            if CONFIG["notes"]:
                self.words[gold].update(_toks(r["input"]))
            
            # Store only hard examples or sample of easy ones
            if CONFIG["store"] == "error_focused":
                if not ok:
                    # Store all errors
                    ex = {"input": r["input"], "target": gold, "hard": True}
                    self.examples.append(ex)
                elif len([e for e in self.examples if not e.get("hard", False)]) < len(self.recent_errors) * 2:
                    # Keep some easy examples for label coverage (2x the recent error count)
                    ex = {"input": r["input"], "target": gold, "hard": False}
                    self.examples.append(ex)
            else:
                # Fallback to storing all
                ex = {"input": r["input"], "target": gold, "hard": not ok}
                if CONFIG["store"] == "per_label_cap":
                    same = [e for e in self.examples if e["target"] == gold]
                    if len(same) >= CONFIG["cap"]:
                        self.examples.remove(same[0])
                self.examples.append(ex)
        
        if CONFIG["store"] == "errors_first":
            self.examples.sort(key=lambda e: not e.get("hard", False))

    # --------------------------------------------------------------- retrieval
    def _similar(self, query: str):
        q = _toks(query)
        scored = []
        for i, e in enumerate(self.examples):
            t = _toks(e["input"])
            sim = len(q & t) / max(1, len(q | t))
            scored.append((sim, i, e))
        scored.sort(key=lambda x: (-x[0], -x[1]))
        return [e for _, _, e in scored]

    def _select(self, query: str):
        """Select demos with adaptive rival selection biased toward recent errors."""
        sel, k = CONFIG["select"], CONFIG["k"]
        
        if sel == "all":
            chosen = list(self.examples)
        elif sel == "recent":
            chosen = self.examples[-k:]
        elif sel == "adaptive_error":
            # Retrieve top-k similar examples
            ranked = self._similar(query)
            chosen = ranked[:k]
            
            # Boost rival selection for recently-confused labels
            top_labels = [e["target"] for e in chosen[:3]]
            
            # Identify recent error pairs that involve top labels
            recent_rivals = set()
            for pred, gold in self.recent_errors:
                if pred in top_labels and gold not in top_labels:
                    recent_rivals.add(gold)
                if gold in top_labels and pred not in top_labels:
                    recent_rivals.add(pred)
            
            # Add demos of recent rivals (prioritize them)
            for lab in recent_rivals:
                for e in ranked:
                    if e["target"] == lab and e not in chosen:
                        chosen.append(e)
                        break
            
            # Also add some all-time confusion rivals if budget allows
            top = [e["target"] for e in chosen[:3]]
            rivals = []
            for (p, g), n in self.confusions.most_common():
                if p in top and g not in top:
                    rivals.append((g, n))
                if g in top and p not in top:
                    rivals.append((p, n))
            
            rivals.sort(key=lambda x: -x[1])  # Sort by confusion count
            for lab, _ in rivals[:2]:  # Add up to 2 all-time rivals
                if lab not in recent_rivals:
                    for e in ranked:
                        if e["target"] == lab and e not in chosen:
                            chosen.append(e)
                            break
        else:
            ranked = self._similar(query)
            chosen = ranked[:k]
        
        # Fit into budget
        out, used = [], 0
        for e in chosen:
            n = len(e["input"]) + len(e["target"]) + 8
            if used + n > CONFIG["char_budget"]:
                break
            out.append(e)
            used += n
        
        if sel not in ("all", "recent"):
            out.reverse()  # most similar demonstration closest to the question
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
        return json.dumps({"examples": self.examples, "labels": self.labels,
                           "confusions": [[p, g, n] for (p, g), n in self.confusions.items()],
                           "words": {k: dict(v) for k, v in self.words.items()},
                           "recent_errors": list(self.recent_errors)})

    def set_state(self, state: str) -> None:
        d = json.loads(state or "{}")
        self.examples = d.get("examples", [])
        self.labels = d.get("labels", [])
        self.confusions = Counter({(p, g): n for p, g, n in d.get("confusions", [])})
        self.words = defaultdict(Counter, {k: Counter(v) for k, v in d.get("words", {}).items()})
        self.recent_errors = deque(d.get("recent_errors", []), maxlen=30)
