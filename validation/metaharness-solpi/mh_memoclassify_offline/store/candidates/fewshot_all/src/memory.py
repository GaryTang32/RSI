"""Few-shot baseline using ALL training examples (global character cap)."""
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

    def _format_examples_section(self, seed=None) -> str:
        if not self.examples:
            return ""
        to_use = list(self.examples[-MAX_EXAMPLES:])
        if seed is not None:
            random.Random(seed).shuffle(to_use)
        parts, total = [], 0
        for ex in to_use:
            part = f"Q: {ex['input']}\nA: {ex['target']}"
            if total + len(part) > MAX_CHARS:
                break
            parts.append(part)
            total += len(part) + 2
        return "\n\n".join(parts)

    def predict(self, input: str) -> tuple[str, dict[str, Any]]:
        section = self._format_examples_section(seed=_seed_for_input(input))
        response = self.call_llm(PROMPT_TEMPLATE.format(examples_section=section, input=input))
        return extract_json_field(response, "final_answer"), {"num_examples": len(self.examples)}

    def learn_from_batch(self, batch_results):
        for r in batch_results:
            self.examples.append({"input": r["input"], "target": r["ground_truth"]})

    def get_context_length(self) -> int:
        return len(self._format_examples_section())

    def get_state(self) -> str:
        return json.dumps({"examples": self.examples})

    def set_state(self, state: str) -> None:
        self.examples = json.loads(state).get("examples", [])
