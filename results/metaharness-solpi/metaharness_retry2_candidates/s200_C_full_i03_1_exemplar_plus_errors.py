"""Memory system: exemplar per label plus recent hard examples."""
import json
import re
from collections import Counter, defaultdict
from typing import Any

CONFIG = {'exemplar_per_label': True,
 'max_recent_errors': 12,
 'char_budget': 1500,
 'demo_chars': 0,
 'label_list': False,
 'learn': 'always',
 'lookup': {},
 'note_words': 6,
 'notes': True,
 'prompt': 'json'}

WORD = re.compile(r"[a-z0-9]{3,}")


def _toks(text: str) -> set:
    return set(WORD.findall(text.lower().split("options:")[0]))


def _norm(s: Any) -> str:
    return str(s or "").strip().strip("`*\"'.,;[]{}() ").lower()


class Memory(MemorySystem):
    """Memory system: exemplar per label plus recent hard examples."""

    def __init__(self, llm):
        super().__init__(llm)
        self.exemplars = {}         # label -> best example
        self.recent_errors = []     # last N hard examples (FIFO)
        self.labels = []            # all labels seen
        self.words = defaultdict(Counter)
        self.all_examples = []      # archive for exemplar selection

    # ---------------------------------------------------------------- learning
    def learn_from_batch(self, batch_results):
        for r in batch_results:
            gold = _norm(r["ground_truth"])
            ok = bool(r.get("was_correct", True))
            if gold not in self.labels:
                self.labels.append(gold)
            if CONFIG["notes"]:
                self.words[gold].update(_toks(r["input"]))
            
            ex = {"input": r["input"], "target": gold, "correct": ok}
            self.all_examples.append(ex)
            
            # Add errors to recent_errors queue
            if not ok:
                self.recent_errors.append(ex)
                if len(self.recent_errors) > CONFIG["max_recent_errors"]:
                    self.recent_errors.pop(0)
            
            # Update exemplar: most recent correct example per label, else most recent
            if gold not in self.exemplars or (ok and not self.exemplars[gold].get("correct", True)):
                self.exemplars[gold] = ex
            elif ok and self.exemplars[gold].get("correct", True):
                # Both correct; keep the more recent
                self.exemplars[gold] = ex

    # --------------------------------------------------------------- retrieval
    def _similar(self, query: str, examples):
        q = _toks(query)
        scored = []
        for i, e in enumerate(examples):
            t = _toks(e["input"])
            sim = len(q & t) / max(1, len(q | t))
            scored.append((sim, i, e))
        scored.sort(key=lambda x: (-x[0], -x[1]))
        return [e for _, _, e in scored]

    def _select(self, query: str):
        """Retrieve: exemplars for likely labels + similar recent errors."""
        out, used = [], 0
        
        # Get exemplars for the 3-4 most common or relevant labels
        labels_by_freq = sorted(set(e["target"] for e in self.all_examples),
                                 key=lambda l: -sum(1 for e in self.all_examples if e["target"] == l))
        for lab in labels_by_freq[:4]:
            if lab in self.exemplars:
                ex = self.exemplars[lab]
                n = len(ex["input"]) + len(ex["target"]) + 8
                if used + n > CONFIG["char_budget"]:
                    break
                out.append(ex)
                used += n
        
        # Add similar recent errors
        if self.recent_errors:
            similar = self._similar(query, self.recent_errors)
            for ex in similar:
                n = len(ex["input"]) + len(ex["target"]) + 8
                if used + n > CONFIG["char_budget"]:
                    break
                if ex not in out:
                    out.append(ex)
                    used += n
        
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
        if self.labels:
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
        return answer, {"n_examples": len(exs), "n_exemplars": len(self.exemplars)}

    def get_state(self) -> str:
        return json.dumps({
            "exemplars": {k: v for k, v in self.exemplars.items()},
            "labels": self.labels,
            "recent_errors": self.recent_errors,
            "words": {k: dict(v) for k, v in self.words.items()}
        })

    def set_state(self, state: str) -> None:
        d = json.loads(state or "{}")
        self.exemplars = d.get("exemplars", {})
        self.labels = d.get("labels", [])
        self.recent_errors = d.get("recent_errors", [])
        self.words = defaultdict(Counter, {k: Counter(v) for k, v in d.get("words", {}).items()})
