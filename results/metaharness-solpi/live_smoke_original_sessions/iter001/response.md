# Iteration 1: Meta-Harness Evolution

Let me read the history to understand the current frontier and then formulate two targeted hypotheses.

```bash
cd /home/user/RSI && find history -type f -name "*.jsonl" -o -name "*.json" | head -20
```

## Analysis

**Current State:**
- Frontier: `fewshot_all` (47.4% accuracy, 8668 context cost) dominates `no_memory` (14%, 191 cost)
- Per-dataset for `fewshot_all`: α=36.8%, β=52.6%, γ=52.6% (cost varies 3k–16k)
- Per-dataset for `no_memory`: α=0%, β=42.1%, γ=0% (cost 191 everywhere)

**Key Observations from Traces:**

1. **fewshot_all mechanism**: Stores ALL training examples; shuffles and truncates to MAX_CHARS on each predict.
   - ds_alpha achieves lowest score (36.8%), suggesting too many irrelevant examples.
   - Traces show: model starts with errors, learns incrementally, but early mistakes might confuse later predictions.
   - Prompt length grows unbounded; the frozen model "degrades when the prompt is very long."

2. **no_memory mechanism**: Zero-shot; still achieves 42% on ds_beta, suggesting the frozen model can infer some patterns without examples.

**Falsifiable Hypotheses:**

1. **Exploitation – Correctness-Prioritized Few-Shot**: Examples the model correctly predicted during training are higher-quality demonstrations. Placing them first in the prompt (before incorrect examples) provides clearer learning signal. Expected outcome: improve accuracy on α, β, γ while keeping context cost stable or lower.

2. **Exploration – Input-Similarity Retrieval**: Instead of using all examples, select only the K most similar to the current input (via token overlap). This removes irrelevant noise and adapts example selection per-task. Expected outcome: trade a small accuracy loss for significant context cost reduction, OR find the sweet spot of relevant examples.

---

## Output

```json
{
  "iteration": 1,
  "candidates": [
    {
      "name": "correct_first_fewshot",
      "base_system": "fewshot_all",
      "hypothesis": "Examples correctly predicted during training provide clearer learning signals; prioritizing them (ordered by recency) improves in-context learning accuracy while maintaining context budget.",
      "axis": "exploitation",
      "components": ["correctness-tracking", "example-reordering", "correct-first-placement"]
    },
    {
      "name": "similarity_retrieval",
      "base_system": "fewshot_all",
      "hypothesis": "Selecting only the K most similar training examples (via token overlap) to each input removes irrelevant noise, reduces prompt bloat, and improves signal-to-noise ratio without sacrificing accuracy.",
      "axis": "exploration",
      "components": ["input-similarity", "content-based-selection", "diversity-pruning"]
    }
  ]
}
```

---

## Candidate 1: correct_first_fewshot

=== FILE: agents/correct_first_fewshot/src/memory.py ===
```python
"""Correctness-prioritized few-shot: correct examples first, recent, then incorrect."""
import hashlib
import json
import random
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
MAX_EXAMPLES = 9999


def _seed_for_input(text: str) -> int:
    return int.from_bytes(hashlib.sha256(text.encode()).digest()[:8], "big")


class Memory(MemorySystem):
    def __init__(self, llm):
        super().__init__(llm)
        self.examples = []
        self.example_correct = {}  # Map example index (position in self.examples) -> bool (was_correct)

    def _format_examples_section(self, seed=None) -> str:
        """Format examples with correct ones first (recent), then incorrect ones."""
        if not self.examples:
            return ""
        
        # Separate examples into correct and incorrect
        correct_exs = []
        incorrect_exs = []
        
        for i, ex in enumerate(self.examples):
            if self.example_correct.get(i, False):
                correct_exs.append((i, ex))
            else:
                incorrect_exs.append((i, ex))
        
        # Reverse to get most recent first (since appended in order)
        correct_exs.reverse()
        incorrect_exs.reverse()
        
        # Build prompt: correct examples first, then incorrect
        parts = []
        total = 0
        
        # Add correct examples first (they are more reliable)
        for idx, ex in correct_exs:
            part = f"Q: {ex['input']}\nA: {ex['target']}"
            if total + len(part) + 2 > MAX_CHARS:
                break
            parts.append(part)
            total += len(part) + 2
        
        # Add incorrect examples for diversity and coverage
        for idx, ex in incorrect_exs:
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
        """Learn from batch, tracking which examples were correctly predicted."""
        for r in batch_results:
            idx = len(self.examples)
            self.examples.append({"input": r["input"], "target": r["ground_truth"]})
            # Mark example as correct if the model's prediction matched ground truth
            self.example_correct[idx] = r.get("was_correct", False)

    def get_state(self) -> str:
        return json.dumps({
            "examples": self.examples,
            "example_correct": self.example_correct
        })

    def set_state(self, state: str) -> None:
        data = json.loads(state)
        self.examples = data.get("examples", [])
        self.example_correct = {int(k): v for k, v in data.get("example_correct", {}).items()}
```

---

## Candidate 2: similarity_retrieval

=== FILE: agents/similarity_retrieval/src/memory.py ===
```python
"""Similarity-based few-shot: retrieve only the K most similar examples per input."""
import hashlib
import json
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
K_RETRIEVE = 15  # Retrieve top-15 most similar examples


def _token_overlap(text1: str, text2: str) -> int:
    """Count overlapping tokens between two texts (case-insensitive)."""
    tokens1 = set(text1.lower().split())
    tokens2 = set(text2.lower().split())
    overlap = len(tokens1 & tokens2)
    return overlap


class Memory(MemorySystem):
    def __init__(self, llm):
        super().__init__(llm)
        self.examples = []

    def _retrieve_similar_examples(self, input_text: str) -> list:
        """Retrieve top-K examples most similar to input by token overlap."""
        if not self.examples:
            return []
        
        # Score each example by token overlap with current input
        scored = []
        for i, ex in enumerate(self.examples):
            overlap = _token_overlap(input_text, ex['input'])
            # Use index as tiebreaker (higher index = more recent)
            scored.append((overlap, i, ex))
        
        # Sort by overlap descending, then by recency (index) descending
        scored.sort(key=lambda x: (-x[0], -x[1]))
        
        # Return top-K
        return [ex for _, _, ex in scored[:K_RETRIEVE]]

    def _format_examples_section(self, input_text: str) -> str:
        """Format retrieved similar examples within character budget."""
        examples = self._retrieve_similar_examples(input_text)
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
        section = self._format_examples_section(input)
        response = self.call_llm(PROMPT_TEMPLATE.format(examples_section=section, input=input))
        return extract_json_field(response, "final_answer"), {"num_examples": len(self.examples)}

    def learn_from_batch(self, batch_results):
        """Store all training examples for later retrieval."""
        for r in batch_results:
            self.examples.append({"input": r["input"], "target": r["ground_truth"]})

    def get_state(self) -> str:
        return json.dumps({"examples": self.examples})

    def set_state(self, state: str) -> None:
        self.examples = json.loads(state).get("examples", [])
```