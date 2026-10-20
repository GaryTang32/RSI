"""Memory system: contrastive + model-learned error lessons."""
import json
import re
from collections import Counter, defaultdict
from typing import Any

CONFIG = {'cap': 6,
 'char_budget': 7000,
 'demo_chars': 0,
 'k': 12,
 'label_list': False,
 'learn': 'always',
 'lookup': {},
 'max_lessons': 50,
 'note_words': 5,
 'notes': False,
 'prompt': 'json',
 'select': 'contrastive',
 'store': 'all'}

WORD = re.compile(r"[a-z0-9]{3,}")


def _toks(text: str) -> set:
    return set(WORD.findall(text.lower().split("options:")[0]))


def _norm(s: Any) -> str:
    return str(s or "").strip().strip("`*\"'.,;[]{}() ").lower()


class Memory(MemorySystem):
    """Memory system: contrastive selection + learned error distinctions."""

    def __init__(self, llm):
        super().__init__(llm)
        self.examples = []          # {"input", "target"}
        self.labels = []            # labels in order of first appearance
        self.confusions = Counter() # (predicted, gold) -> count, learned from online mistakes
        self.words = defaultdict(Counter)
        self.lessons = []           # [{"from": pred_label, "to": gold_label, "lesson": text}, ...]

    # ---------------------------------------------------------------- learning
    def learn_from_batch(self, batch_results):
        for r in batch_results:
            gold = _norm(r["ground_truth"])
            pred = _norm(r.get("prediction", ""))
            ok = bool(r.get("was_correct", True))
            
            if gold not in self.labels:
                self.labels.append(gold)
            
            # Track confusions and generate lessons on errors
            if not ok:
                self.confusions[(pred, gold)] += 1
                
                # Generate a lesson explaining the difference (offline, doesn't count per-query cost)
                if len(self.lessons) < CONFIG["max_lessons"]:
                    try:
                        lesson_prompt = (
                            f"Briefly explain why '{pred}' is wrong and '{gold}' is correct for: {r['input'][:150]}\n"
                            f"Focus on distinguishing features or context clues."
                        )
                        lesson_text = self.call_llm(lesson_prompt)
                        # Keep only first 250 chars of lesson to avoid explosion
                        lesson_text = lesson_text.strip()[:250]
                        self.lessons.append({"from": pred, "to": gold, "lesson": lesson_text})
                    except Exception:
                        pass  # Silently skip on error
            
            # Store example
            if CONFIG["notes"]:
                self.words[gold].update(_toks(r["input"]))
            if CONFIG["learn"] == "errors_only" and ok and any(e["target"] == gold for e in self.examples):
                continue
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
        sel, k = CONFIG["select"], CONFIG["k"]
        if sel == "all":
            chosen = list(self.examples)
        elif sel == "recent":
            chosen = self.examples[-k:]
        else:
            ranked = self._similar(query)
            chosen = ranked[:k]
            if sel == "topk_coverage":
                have = {e["target"] for e in chosen}
                for e in ranked[k:]:
                    if e["target"] not in have:
                        chosen.append(e)
                        have.add(e["target"])
            elif sel == "contrastive":
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

    def _relevant_lessons(self, top_labels: list) -> str:
        """Get lessons relevant to the top predicted labels."""
        relevant = []
        for lesson in self.lessons:
            if lesson["from"] in top_labels or lesson["to"] in top_labels:
                relevant.append(f"- {lesson['from']} vs {lesson['to']}: {lesson['lesson']}")
        if relevant:
            return "\n".join(relevant[:3])  # Top 3 lessons only
        return ""

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
        
        # Include relevant lessons for top predicted labels
        top_labels = [e["target"] for e in exs[:3]]
        lessons_text = self._relevant_lessons(top_labels)
        if lessons_text:
            parts.append("Label distinctions:\n" + lessons_text)
        
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
        return answer, {"n_examples": len(exs), "n_lessons": len(self.lessons)}

    def get_state(self) -> str:
        return json.dumps({"examples": self.examples, "labels": self.labels,
                           "confusions": [[p, g, n] for (p, g), n in self.confusions.items()],
                           "words": {k: dict(v) for k, v in self.words.items()},
                           "lessons": self.lessons})

    def set_state(self, state: str) -> None:
        d = json.loads(state or "{}")
        self.examples = d.get("examples", [])
        self.labels = d.get("labels", [])
        self.confusions = Counter({(p, g): n for p, g, n in d.get("confusions", [])})
        self.words = defaultdict(Counter, {k: Counter(v) for k, v in d.get("words", {}).items()})
        self.lessons = d.get("lessons", [])
