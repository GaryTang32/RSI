"""Memory system: semantic-guided retrieval using label keywords."""
import json
import re
from collections import Counter, defaultdict
from typing import Any

CONFIG = {'cap': 6,
 'char_budget': 5500,
 'demo_chars': 0,
 'k': 8,
 'label_list': True,
 'learn': 'always',
 'lookup': {},
 'note_words': 6,
 'notes': False,
 'prompt': 'json',
 'select': 'semantic_topk',
 'store': 'all'}

WORD = re.compile(r"[a-z0-9]{3,}")


def _toks(text: str) -> set:
    return set(WORD.findall(text.lower().split("options:")[0]))


def _norm(s: Any) -> str:
    return str(s or "").strip().strip("`*\"'.,;[]{}() ").lower()


class Memory(MemorySystem):
    """Retrieve examples based on semantic match between input and label keywords."""

    def __init__(self, llm):
        super().__init__(llm)
        self.examples = []
        self.labels = []
        self.confusions = Counter()
        self.words = defaultdict(Counter)
        self.label_keywords = {}  # distinctive keywords per label

    # ---------------------------------------------------------------- learning
    def learn_from_batch(self, batch_results):
        for r in batch_results:
            gold = _norm(r["ground_truth"])
            ok = bool(r.get("was_correct", True))
            if gold not in self.labels:
                self.labels.append(gold)
            if not ok:
                self.confusions[(_norm(r.get("prediction")), gold)] += 1
            # Always track words per label for semantic descriptions
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
        # Update semantic keywords
        self._update_keywords()

    def _update_keywords(self):
        """Compute TF-IDF style distinctive keywords per label."""
        if not self.words:
            return
        # Document frequency: how many labels contain each word
        df = Counter()
        for label_words in self.words.values():
            df.update(set(label_words.keys()))
        
        for lab in self.labels:
            word_counts = self.words.get(lab)
            if not word_counts:
                self.label_keywords[lab] = []
                continue
            # Score words by TF-IDF: high in this label, low in others
            scored = []
            for w, tf in word_counts.items():
                idf = 1.0 / (1.0 + df.get(w, 1))  # penalize common words
                score = tf * idf
                scored.append((score, w))
            scored.sort(key=lambda x: -x[0])
            self.label_keywords[lab] = [w for _, w in scored[:CONFIG["note_words"]]]

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
        elif sel == "semantic_topk":
            # Retrieve by combining token similarity with semantic keyword match
            q = _toks(query)
            scored = []
            for e in self.examples:
                label = e["target"]
                label_kw = set(self.label_keywords.get(label, []))
                
                # How many label keywords match the query?
                keyword_overlap = len(q & label_kw) / max(1, len(label_kw)) if label_kw else 0
                
                # Token similarity (unchanged)
                t = _toks(e["input"])
                token_sim = len(q & t) / max(1, len(q | t))
                
                # Combined: weight toward semantic match (60% keyword, 40% token)
                combined = 0.6 * keyword_overlap + 0.4 * token_sim
                scored.append((combined, token_sim, e))
            
            scored.sort(key=lambda x: (-x[0], -x[1]))
            chosen = [e for _, _, e in scored[:k]]
        else:  # topk (default)
            ranked = self._similar(query)
            chosen = ranked[:k]
        
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

    # -------------------------------------------------------------- prediction
    def predict(self, input: str):
        for key, lab in CONFIG["lookup"].items():
            if key in input:
                return lab, {"lookup": True}
        
        parts = ["Classify the input into one of the labels, following the demonstrations."]
        
        # Show label keywords to give semantic context
        if CONFIG["label_list"] and self.labels and self.label_keywords:
            kw_lines = []
            for lab in self.labels[:12]:  # limit to prevent overflow
                kw = self.label_keywords.get(lab, [])
                if kw:
                    kw_lines.append(f"{lab}: {' '.join(kw[:3])}")
                else:
                    kw_lines.append(lab)
            parts.append("Labels and keywords: " + "; ".join(kw_lines))
        
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
                           "label_keywords": self.label_keywords})

    def set_state(self, state: str) -> None:
        d = json.loads(state or "{}")
        self.examples = d.get("examples", [])
        self.labels = d.get("labels", [])
        self.confusions = Counter({(p, g): n for p, g, n in d.get("confusions", [])})
        self.words = defaultdict(Counter, {k: Counter(v) for k, v in d.get("words", {}).items()})
        self.label_keywords = d.get("label_keywords", {})
