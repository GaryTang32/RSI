"""Memory system: hard examples prioritized in retrieval."""
import json
import re
from collections import Counter, defaultdict
from typing import Any

CONFIG = {'cap': 6,
 'char_budget': 1500,
 'demo_chars': 0,
 'k': 4,
 'label_list': False,
 'learn': 'always',
 'lookup': {},
 'note_words': 6,
 'notes': True,
 'prompt': 'json',
 'select': 'topk',
 'store': 'all'}

WORD = re.compile(r"[a-z0-9]{3,}")


def _toks(text: str) -> set:
    return set(WORD.findall(text.lower().split("options:")[0]))


def _norm(s: Any) -> str:
    return str(s or "").strip().strip("`*\"'.,;[]{}() ").lower()


class Memory(MemorySystem):
    """Memory system: hard examples prioritized in retrieval."""

    def __init__(self, llm):
        super().__init__(llm)
        self.examples = []          # all {"input", "target", "hard"}
        self.hard_examples = []     # only errors, subset of examples
        self.labels = []
        self.confusions = Counter()
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
            if CONFIG["notes"]:
                self.words[gold].update(_toks(r["input"]))
            ex = {"input": r["input"], "target": gold, "hard": not ok}
            self.examples.append(ex)
            if not ok:
                # Store errors in separate pool for prioritization
                self.hard_examples.append(ex)

    # --------------------------------------------------------------- retrieval
    def _similar(self, query: str, examples=None):
        if examples is None:
            examples = self.examples
        q = _toks(query)
        scored = []
        for i, e in enumerate(examples):
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
            # For topk: prioritize hard examples first
            hard_ranked = self._similar(query, self.hard_examples) if self.hard_examples else []
            all_ranked = self._similar(query, self.examples)
            
            # Take top hard examples first (up to 3), then fill with regular topk
            hard_to_use = hard_ranked[:3]
            hard_set = {id(e) for e in hard_to_use}
            regular_to_use = [e for e in all_ranked if id(e) not in hard_set][:max(0, k - len(hard_to_use))]
            
            chosen = hard_to_use + regular_to_use
            
            if sel == "topk_coverage":
                have = {e["target"] for e in chosen}
                for e in all_ranked:
                    if e["target"] not in have:
                        chosen.append(e)
                        have.add(e["target"])
        
        out, used = [], 0
        for e in chosen:
            n = len(e["input"]) + len(e["target"]) + 8
            if used + n > CONFIG["char_budget"]:
                break
            out.append(e)
            used += n
        if sel not in ("all", "recent"):
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
        return answer, {"n_examples": len(exs), "n_hard": len(self.hard_examples)}

    def get_state(self) -> str:
        return json.dumps({"examples": self.examples, "labels": self.labels,
                           "confusions": [[p, g, n] for (p, g), n in self.confusions.items()],
                           "words": {k: dict(v) for k, v in self.words.items()}})

    def set_state(self, state: str) -> None:
        d = json.loads(state or "{}")
        self.examples = d.get("examples", [])
        self.labels = d.get("labels", [])
        self.confusions = Counter({(p, g): n for p, g, n in d.get("confusions", [])})
        self.words = defaultdict(Counter, {k: Counter(v) for k, v in d.get("words", {}).items()})
        # Rebuild hard_examples from marked examples
        self.hard_examples = [e for e in self.examples if e.get("hard", False)]
