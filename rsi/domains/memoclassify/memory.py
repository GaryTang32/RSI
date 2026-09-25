"""The harness interface of MemoClassify: a *memory system* around a frozen LLM.

Port of ``reference_examples/text_classification/memory_system.py`` (Meta-Harness):

* ``predict(input) -> (answer, metadata)`` - called BEFORE the label is seen;
* ``learn_from_batch(batch_results)`` - called after each batch with dicts
  ``{input, prediction, ground_truth, was_correct, metadata}``;
* ``get_state() / set_state(state)`` - serialisable memory (checkpoints, test
  finalisation reloads it);
* ``call_llm(prompt)`` - the only way to reach the frozen model; it records the
  last prompt's length / hash / text so the domain can measure context cost.

A candidate harness is a single file ``memory.py`` defining ``class Memory``
(any ``MemorySystem`` subclass is accepted). ``MemorySystem`` and
``extract_json_field`` are pre-imported into the candidate's namespace and are
also importable from ``rsi.domains.memoclassify.memory``.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from abc import ABC, abstractmethod
from typing import Any, Callable


def extract_json_field(text: str, field: str, default: str = "") -> str:
    """Extract ``field`` from JSON in an LLM reply (direct, fenced, balanced braces, regex)."""
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return str(data.get(field, default))
    except (json.JSONDecodeError, TypeError):
        pass
    for match in re.finditer(r"```(?:json)?\s*([\s\S]*?)\s*```", text or ""):
        try:
            data = json.loads(match.group(1))
            if isinstance(data, dict):
                return str(data.get(field, default))
        except json.JSONDecodeError:
            pass
    s = text or ""
    for start in range(len(s)):
        if s[start] != "{":
            continue
        depth, pos, in_str = 1, start + 1, False
        while pos < len(s) and depth > 0:
            c = s[pos]
            if c == '"' and s[pos - 1] != "\\":
                in_str = not in_str
            elif not in_str:
                depth += 1 if c == "{" else (-1 if c == "}" else 0)
            pos += 1
        if depth == 0:
            cand = re.sub(r",\s*([\]}])", r"\1", s[start:pos])
            try:
                data = json.loads(cand)
                if isinstance(data, dict):
                    return str(data.get(field, default))
            except json.JSONDecodeError:
                pass
    m = re.findall(rf'"{field}"\s*:\s*"([^"]*)"', s)
    if m:
        return m[-1]
    m = re.findall(r"answer\s*[:=]\s*(.+)", s, flags=re.I)
    return m[-1].strip() if m else default


class MemorySystem(ABC):
    """Memory system interface for online/offline learning (see module docstring)."""

    def __init__(self, llm: Callable[[str], str]):
        self._llm = llm
        self._prompt_local = threading.local()

    def call_llm(self, prompt: str) -> str:
        self._prompt_local.last_prompt_len = len(prompt)
        self._prompt_local.last_prompt_hash = hashlib.md5(prompt.encode()).hexdigest()[:8]
        self._prompt_local.last_prompt_text = prompt
        return self._llm(prompt)

    def get_last_prompt_info(self) -> dict[str, Any]:
        return {"prompt_len": getattr(self._prompt_local, "last_prompt_len", None),
                "prompt_hash": getattr(self._prompt_local, "last_prompt_hash", None),
                "prompt_text": getattr(self._prompt_local, "last_prompt_text", None)}

    @abstractmethod
    def predict(self, input: str) -> tuple[str, dict[str, Any]]:
        """Prediction BEFORE seeing ground truth."""

    @abstractmethod
    def learn_from_batch(self, batch_results: list[dict[str, Any]]) -> None:
        """Learn from ``[{input, prediction, ground_truth, was_correct, metadata}]``."""

    def get_context_length(self) -> int:
        return len(self.get_state())

    @abstractmethod
    def get_state(self) -> str:
        """Serialisable state."""

    @abstractmethod
    def set_state(self, state: str) -> None:
        """Restore state."""


# ---------------------------------------------------------------- seed programs
NO_MEMORY = '''"""NoMemory baseline - no learning, direct prompting."""
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
'''

FEWSHOT_ALL = '''"""Few-shot baseline using ALL training examples (global character cap)."""
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
            part = f"Q: {ex['input']}\\nA: {ex['target']}"
            if total + len(part) > MAX_CHARS:
                break
            parts.append(part)
            total += len(part) + 2
        return "\\n\\n".join(parts)

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
'''

SEED_PROGRAMS = {"no_memory": NO_MEMORY, "fewshot_all": FEWSHOT_ALL}

#: Few-shot with at most N examples (port of the release's ``agents/fewshot_memory.py``: a random sample of N
#: stored examples per query, shuffled, under the same global character cap). The paper's Table 2 reports
#: few-shot with N in {4, 8, 16, 32, all}. These are *comparators*, not part of the initial population (the
#: release's ``config.yaml`` baselines are ``no_memory`` and ``fewshot_all``): ``fewshot_all`` overflows
#: MemoLM-A's context budget on two of three search datasets, so it is a weak comparator (audit N9), and
#: "less context than fewshot_all" should be checked against the best of these as well.
FEWSHOT_N = FEWSHOT_ALL.replace('"""Few-shot baseline using ALL training examples (global character cap)."""',
                                '"""Few-shot baseline using at most MAX_EXAMPLES random stored examples per query."""') \
    .replace("MAX_EXAMPLES = 9999", "MAX_EXAMPLES = {n}") \
    .replace("""        to_use = list(self.examples[-MAX_EXAMPLES:])
        if seed is not None:
            random.Random(seed).shuffle(to_use)""", """        if seed is not None and len(self.examples) > MAX_EXAMPLES:
            to_use = random.Random(seed).sample(self.examples, MAX_EXAMPLES)
        else:
            to_use = list(self.examples[-MAX_EXAMPLES:])
            if seed is not None:
                random.Random(seed).shuffle(to_use)""")


def fewshot_program(n: int) -> str:
    """``memory.py`` source of the few-shot-N comparator (see :data:`FEWSHOT_N`)."""
    return FEWSHOT_N.replace("MAX_EXAMPLES = {n}", f"MAX_EXAMPLES = {int(n)}")


COMPARATOR_PROGRAMS = {f"fewshot_{n}": fewshot_program(n) for n in (4, 8, 16, 32, 64)}
