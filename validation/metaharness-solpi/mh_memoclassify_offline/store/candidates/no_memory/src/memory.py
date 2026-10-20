"""NoMemory baseline - no learning, direct prompting."""
from typing import Any

PROMPT = """Answer the following question.

{input}

**Answer in this exact JSON format:**
{{
  "reasoning": "[Your chain of thought / reasoning process]",
  "final_answer": "[Your concise final answer here]"
}}
"""


class Memory(MemorySystem):
    """Baseline that does not learn - just prompts the LLM directly."""

    def __init__(self, llm):
        super().__init__(llm)
        self._state = "{}"

    def predict(self, input: str) -> tuple[str, dict[str, Any]]:
        response = self.call_llm(PROMPT.format(input=input))
        return extract_json_field(response, "final_answer"), {"full_response": response}

    def learn_from_batch(self, batch_results):
        pass

    def get_state(self) -> str:
        return self._state

    def set_state(self, state: str) -> None:
        self._state = state
