```json
{
  "iteration": 2,
  "candidates": [
    {
      "name": "weighted_recency_correctness",
      "base_system": "fewshot_all",
      "hypothesis": "Recent examples provide fresh training context, and examples the model correctly predicted during training are stronger learning signals. A smooth exponential decay on recency combined with a correctness multiplier will weight high-signal examples higher, improving accuracy without exceeding context budget.",
      "axis": "exploitation",
      "components": ["recency-decay", "correctness-weighting", "smooth-scoring"]
    },
    {
      "name": "label_stratified_diverse",
      "base_system": "fewshot_all",
      "hypothesis": "Ensuring diverse output labels in few-shot examples improves generalization. By stratifying retrieval to show one correct example per unique output label seen during training, the model learns varied output patterns rather than being biased by frequency, reducing overfitting to common labels.",
      "axis": "exploration",
      "components": ["output-label-clustering", "stratified-sampling", "label-diversity"]
    }
  ]
}
```

---

## Candidate 1: weighted_recency_correctness

=== FILE: agents/weighted_recency_correctness/src/memory.py ===
```python
"""Recency and correctness-weighted few-shot selection."""
import json
import math
from typing import Any

PROMPT_TEMPLATE = """Solve the problem below based on the examples provided.

{examples_section}

**Problem:**
{input}

**Instructions:**
- Follow the patterns shown in the examples above
- Respond in JSON format

{{"reasoning": "[your reasoning]", "final_answer": "[your answer]"}}"""

MAX_CHARS = 30000
K_RETRIEVE = 20
RECENCY_DECAY = 0.08  # Exponential decay: weight of example i = exp(-decay * (current - i))
CORRECTNESS_BONUS = 2.0  # Multiplier for examples the model got correct


class Memory(MemorySystem):
    def __init__(self, llm):
        super().__init__(llm)
        self.examples = []
        self.correctness = []  # Parallel list: correctness[i] = was_correct for examples[i]

    def _score_example(self, idx: int) -> float:
        """Score an example by recency (exponential decay) and correctness."""
        current_idx = len(self.examples) - 1
        distance = current_idx - idx
        
        # Exponential decay favors recent examples
        recency_score = math.exp(-RECENCY_DECAY * distance)
        
        # Correctness multiplier: examples the model got right are worth more
        is_correct = self.correctness[idx] if idx < len(self.correctness) else False
        correctness_factor = CORRECTNESS_BONUS if is_correct else 1.0
        
        return recency_score * correctness_factor

    def _retrieve_scored_examples(self) -> list:
        """Retrieve top-K examples by combined recency+correctness score."""
        if not self.examples:
            return []
        
        scored = []
        for i, ex in enumerate(self.examples):
            score = self._score_example(i)
            scored.append((score, i, ex))
        
        # Sort by score descending (highest-scoring examples first)
        scored.sort(key=lambda x: -x[0])
        
        # Return top-K examples
        return [ex for _, _, ex in scored[:K_RETRIEVE]]

    def _format_examples_section(self) -> str:
        """Format retrieved examples within character budget."""
        examples = self._retrieve_scored_examples()
        if not examples:
            return ""
        
        parts = []
        total = 0
        
        for ex in examples:
            part = f"Q: {ex['input']}\nA: {ex['target']}"
            if total + len(part) + 2 > MAX_CHARS:
                break
            parts.append(part)
            total += len(part) + 2
        
        return "\n\n".join(parts)

    def predict(self, input: str) -> tuple[str, dict[str, Any]]:
        section = self._format_examples_section()
        response = self.call_llm(PROMPT_TEMPLATE.format(examples_section=section, input=input))
        return extract_json_field(response, "final_answer"), {"num_examples": len(self.examples)}

    def learn_from_batch(self, batch_results):
        """Store examples and track which were correctly predicted."""
        for r in batch_results:
            self.examples.append({"input": r["input"], "target": r["ground_truth"]})
            self.correctness.append(r.get("was_correct", False))

    def get_state(self) -> str:
        return json.dumps({
            "examples": self.examples,
            "correctness": self.correctness
        })

    def set_state(self, state: str) -> None:
        data = json.loads(state)
        self.examples = data.get("examples", [])
        self.correctness = data.get("correctness", [])
```

---

## Candidate 2: label_stratified_diverse

=== FILE: agents/label_stratified_diverse/src/memory.py ===
```python
"""Label-stratified few-shot: ensure diverse output patterns in demonstrations."""
import json
from typing import Any
from collections import defaultdict

PROMPT_TEMPLATE = """Solve the problem below based on the examples provided.

{examples_section}

**Problem:**
{input}

**Instructions:**
- Follow the patterns shown in the examples above
- Respond in JSON format

{{"reasoning": "[your reasoning]", "final_answer": "[your answer]"}}"""

MAX_CHARS = 30000
MAX_LABELS_PER_BATCH = 20  # Maximum unique labels to showcase


class Memory(MemorySystem):
    def __init__(self, llm):
        super().__init__(llm)
        self.examples = []
        self.correctness = []
        self.label_to_indices = defaultdict(list)  # output_label -> [indices in self.examples]

    def _retrieve_diverse_by_label(self) -> list:
        """
        Retrieve examples stratified by output label.
        Goal: Show one high-quality example per label to maximize pattern diversity.
        """
        if not self.examples:
            return []
        
        retrieved = []
        labels_seen = list(self.label_to_indices.keys())[:MAX_LABELS_PER_BATCH]
        
        # First pass: add one example per unique label (prefer correct, recent)
        for label in labels_seen:
            indices = self.label_to_indices[label]
            
            # Find the most recent correct example for this label, else most recent
            best_idx = None
            for idx in reversed(indices):  # Most recent first
                if idx < len(self.correctness) and self.correctness[idx]:
                    best_idx = idx
                    break
            
            # If no correct example, use the most recent one
            if best_idx is None:
                best_idx = indices[-1]
            
            retrieved.append((best_idx, self.examples[best_idx]))
        
        # If still under budget, add additional recent examples for extra diversity
        all_used = set(idx for idx, _ in retrieved)
        all_indices = list(range(len(self.examples)))
        
        for idx in reversed(all_indices):  # Most recent first
            if len(retrieved) >= MAX_LABELS_PER_BATCH * 2:
                break
            if idx not in all_used:
                retrieved.append((idx, self.examples[idx]))
                all_used.add(idx)
        
        # Sort by original index to preserve order in context
        retrieved.sort(key=lambda x: x[0])
        return [ex for _, ex in retrieved]

    def _format_examples_section(self) -> str:
        """Format retrieved examples within character budget."""
        examples = self._retrieve_diverse_by_label()
        if not examples:
            return ""
        
        parts = []
        total = 0
        
        for ex in examples:
            part = f"Q: {ex['input']}\nA: {ex['target']}"
            if total + len(part) + 2 > MAX_CHARS:
                break
            parts.append(part)
            total += len(part) + 2
        
        return "\n\n".join(parts)

    def predict(self, input: str) -> tuple[str, dict[str, Any]]:
        section = self._format_examples_section()
        response = self.call_llm(PROMPT_TEMPLATE.format(examples_section=section, input=input))
        return extract_json_field(response, "final_answer"), {"num_examples": len(self.examples)}

    def learn_from_batch(self, batch_results):
        """Store examples, track correctness, and build label->indices mapping."""
        for r in batch_results:
            idx = len(self.examples)
            target = r["ground_truth"]
            
            self.examples.append({"input": r["input"], "target": target})
            self.correctness.append(r.get("was_correct", False))
            self.label_to_indices[target].append(idx)

    def get_state(self) -> str:
        return json.dumps({
            "examples": self.examples,
            "correctness": self.correctness,
            "label_to_indices": {str(k): v for k, v in self.label_to_indices.items()}
        })

    def set_state(self, state: str) -> None:
        data = json.loads(state)
        self.examples = data.get("examples", [])
        self.correctness = data.get("correctness", [])
        
        label_indices_dict = data.get("label_to_indices", {})
        self.label_to_indices = defaultdict(list)
        for label, indices in label_indices_dict.items():
            self.label_to_indices[label] = indices
```