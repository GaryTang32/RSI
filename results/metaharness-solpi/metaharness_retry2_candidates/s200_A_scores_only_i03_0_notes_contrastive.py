"""Memory system: notes + light contrastive selection."""
import json
import re
from collections import Counter, defaultdict
from typing import Any

CONFIG = {
    'cap': 6,
    'char_budget': 1200,
    'demo_chars': 0,
    'k': 3,
    'label_list': True,
    'learn': 'always',
    'lookup': {},
    'note_words': 6,
    'notes': True,
    'prompt': 'json',
    'select': 'topk_contrastive',
    'store': 'all'
}

WORD = re.compile(r"[a-z0-9]{3,}")


def _toks(text: str) -> set:
    return set(WORD.findall(text.lower().split("options:")[0]))


def _norm(s: Any) -> str:
    return str(s or "").strip().strip("`*\"'.,;[]{}() ").lower()


class Memory(MemorySystem):
    """Memory system: notes + light contrastive selection."""

    def __init__(self, llm):
        super().__init__(llm)
        self.examples = []
        self.labels = []
        self.confusions = Counter()
        self.words = defaultdict(Counter)

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
        ranked = self._similar(query)
        chosen = ranked[:CONFIG['k']]

        # Light contrastive: add 1-2 examples of labels confused with top selections
        if CONFIG['select'] == 'topk_contrastive':
            top_labels = set(e["target"] for e in chosen)
            rivals = []
            for (pred, gold), count in self.confusions.most_common(10):
                if pred in top_labels and gold not in top_labels:
                    rivals.append(gold)
                elif gold in top_labels and pred not in top_labels:
                    rivals.append(pred)
            rivals = list(set(rivals))[:2]

            for rival_lab in rivals:
                for e in ranked:
                    if e["target"] == rival_lab and e not in chosen:
                        chosen.append(e)
                        break

        # Budget constraint
        out, used = [], 0
        for e in chosen:
            n = len(e["input"]) + len(e["target"]) + 8
            if used + n > CONFIG["char_budget"]:
                break
            out.append(e)
            used += n

        if CONFIG['select'] not in ("all", "recent"):
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

    def predict(self, input: str):
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
        parts.append('Respond in JSON: {"reasoning": "...", "final_answer": "<label>"}')

        response = self.call_llm("\n\n".join(parts))
        answer = extract_json_field(response, "final_answer")
        return answer, {"n_examples": len(exs)}

    def get_state(self) -> str:
        return json.dumps({
            "examples": self.examples,
            "labels": self.labels,
            "confusions": [[p, g, n] for (p, g), n in self.confusions.items()],
            "words": {k: dict(v) for k, v in self.words.items()}
        })

    def set_state(self, state: str) -> None:
        d = json.loads(state or "{}")
        self.examples = d.get("examples", [])
        self.labels = d.get("labels", [])
        self.confusions = Counter({(p, g): n for p, g, n in d.get("confusions", [])})
        self.words = defaultdict(Counter, {k: Counter(v) for k, v in d.get("words", {}).items()})
