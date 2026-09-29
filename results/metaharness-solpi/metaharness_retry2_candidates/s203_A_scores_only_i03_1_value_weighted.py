"""Memory system: topk retrieval with value-weighted selection learned from online outcomes."""
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
 'store': 'all',
 'value_boost': 1.15,
 'value_decay': 0.85,
 'value_min': 0.1,
 'value_max': 5.0}

WORD = re.compile(r"[a-z0-9]{3,}")


def _toks(text: str) -> set:
    return set(WORD.findall(text.lower().split("options:")[0]))


def _norm(s: Any) -> str:
    return str(s or "").strip().strip("`*\"'.,;[]{}() ").lower()


class Memory(MemorySystem):
    """Topk retrieval with per-example value scores learned from prediction outcomes."""

    def __init__(self, llm):
        super().__init__(llm)
        self.examples = []          # {"input", "target", "hard", "value"}
        self.labels = []            # labels in order of first appearance
        self.confusions = Counter() # (predicted, gold) -> count
        self.words = defaultdict(Counter)

    # ---------------------------------------------------------------- learning
    def learn_from_batch(self, batch_results):
        for r in batch_results:
            gold = _norm(r["ground_truth"])
            ok = bool(r.get("was_correct", True))
            if gold not in self.labels:
                self.labels.append(gold)
            if not ok:
                self.confusions[(_norm(r.get("prediction")), gold)] += 1
            
            # Update value of retrieved examples based on outcome
            meta = r.get("metadata", {})
            retrieved_keys = meta.get("retrieved_keys", [])
            for e in self.examples:
                key = f"{e['input']}|{e['target']}"
                if key in retrieved_keys:
                    if ok:
                        # Boost value when this example was retrieved and we got it right
                        e["value"] = min(e.get("value", 1.0) * CONFIG["value_boost"], CONFIG["value_max"])
                    else:
                        # Decay value when this example was retrieved and we got it wrong
                        e["value"] = max(e.get("value", 1.0) * CONFIG["value_decay"], CONFIG["value_min"])
            
            if CONFIG["notes"]:
                self.words[gold].update(_toks(r["input"]))
            if CONFIG["learn"] == "errors_only" and ok and any(e["target"] == gold for e in self.examples):
                continue
            
            ex = {"input": r["input"], "target": gold, "hard": not ok, "value": 1.0}
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
        sel, k = CONFIG["select"], CONFIG["k"]
        if sel == "all":
            chosen = list(self.examples)
        elif sel == "recent":
            chosen = self.examples[-k:]
        else:
            # Score by (similarity * value) to weight by empirical utility
            q = _toks(query)
            scored = []
            for i, e in enumerate(self.examples):
                t = _toks(e["input"])
                sim = len(q & t) / max(1, len(q | t))
                value_weighted_score = sim * e.get("value", 1.0)
                scored.append((value_weighted_score, i, e))
            scored.sort(key=lambda x: -x[0])
            chosen = [e for _, _, e in scored[:k]]
            
            if sel == "topk_coverage":
                have = {e["target"] for e in chosen}
                for _, _, e in scored[k:]:
                    if e["target"] not in have:
                        chosen.append(e)
                        have.add(e["target"])
            elif sel == "contrastive":
                ranked = self._similar(query)
                chosen = ranked[:k]
                top = [e["target"] for e in chosen[:3]]
                rivals = []
                for (p, g), n in self.confusions.most_common():
                    if p in top and g not in top:
                        rivals.append(g)
                    if g in top and p not in top:
                        rivals.append(p)
                for lab in rivals[:4]:
                    for e in ranked:
                        if e["target"] == lab and e not in chosen:
                            chosen.append(e)
                            break
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
        # Track which examples were retrieved for learning feedback
        retrieved_keys = [f"{e['input']}|{e['target']}" for e in exs]
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
        return answer, {"n_examples": len(exs), "retrieved_keys": retrieved_keys}

    def get_state(self) -> str:
        return json.dumps({"examples": self.examples, "labels": self.labels,
                           "confusions": [[p, g, n] for (p, g), n in self.confusions.items()],
                           "words": {k: dict(v) for k, v in self.words.items()}})

    def set_state(self, state: str) -> None:
        d = json.loads(state or "{}")
        self.examples = d.get("examples", [])
        # Ensure backward compatibility: all examples should have value
        for ex in self.examples:
            if "value" not in ex:
                ex["value"] = 1.0
        self.labels = d.get("labels", [])
        self.confusions = Counter({(p, g): n for p, g, n in d.get("confusions", [])})
        self.words = defaultdict(Counter, {k: Counter(v) for k, v in d.get("words", {}).items()})
```

---

```json
{
  "iteration": 3,
  "candidates": [
    {
      "name": "i03_0_selective_learning",
      "base_system": "i01_0_retrieve_topk",
      "hypothesis": "Learning only from errors plus one baseline success per label reduces noise and redundancy in the demonstration pool, improving accuracy by eliminating redundant successes while keeping high-value error examples.",
      "axis": "exploration",
      "components": ["selective_learning", "axis:E"]
    },
    {
      "name": "i03_1_value_weighted",
      "base_system": "i01_0_retrieve_topk",
      "hypothesis": "Tracking which retrieved examples lead to correct predictions and weighting them higher on future retrievals improves accuracy by learning empirical utility beyond text similarity alone.",
      "axis": "exploitation",
      "components": ["value_weighting", "axis:B", "axis:E"]
    }
  ]
}
