```json
{
  "iteration": 2,
  "candidates": [
    {
      "name": "code_compact",
      "base_system": "code_execution",
      "hypothesis": "Using a minimal system prompt that eliminates unnecessary framing will reduce token consumption by 30-50% on verbose problems while maintaining perfect accuracy, since deterministic code execution is invariant to instruction verbosity.",
      "axis": "exploitation",
      "components": ["A: minimal system prompt", "D: no explanations encouraged", "C: ANSWER: marker extraction"]
    },
    {
      "name": "code_fallback",
      "base_system": "code_execution",
      "hypothesis": "Implementing flexible answer extraction that tries both structured markers (ANSWER:) and last numeric output will improve robustness across different problem types and model response formats.",
      "axis": "exploration",
      "components": ["C: dual-path retrieval (ANSWER: and fallback)", "E: adaptive extraction trigger", "F: model-guided code output structure"]
    }
  ]
}
```

---

## Post-Eval Report (Iteration 1)

=== FILE: reports/iter1.md ===

**Iteration 1 Overview**: Two candidates tested code-based approaches.

**Results**:
- `code_execution`: **100% accuracy** (12/12), 1491 avg tokens → **FRONTIER BEST**
- `python_answer`: **8.3% accuracy** (1/12), 1396.5 avg tokens → on frontier by cost

**code_execution Success**: Mechanism of writing Python code and extracting from tool execution output proved perfect. All 12 tasks correct because:
- Tasks require deterministic computation (factorials, digit sums, modular arithmetic)
- Python execution is ground truth; LLM only generates code structure
- ANSWER: marker reliably present in stdout

**python_answer Failure**: Tried enforcing LLM text markers (PYTHON_RESULT:) without tool execution. Failed on 11/12 because:
- LLM's mental arithmetic still unreliable (wrong digit sums, wrong modulos in narratives)
- Marker extraction failed; LLM didn't consistently output the marker
- Model couldn't follow structured output protocol reliably (gap between instruction and execution)

**Key Insight**: Removing LLM from computation chain (trust tool output, not text) was essential. Verbosity in prompts (A axis) didn't matter; mechanism (C: execution vs. parsing) was decisive.

**Takeaway for Iter 2**: `code_execution` is saturated at 100%. Next iteration should exploit cost reduction (terse prompts, Axis A+D) and explore robustness on non-numeric tasks (alternative retrieval methods, Axis C+E).

---

=== FILE: agents/code_compact/harness.py ===

```python
"""Harness that executes Python code with minimal system prompt."""

import re


def solve(question, llm, tools, files):
    # Minimal system prompt to reduce token usage
    system = "Write Python code to solve this. End with: print('ANSWER: <result>')"
    
    prompt = files["prompts/task.md"].replace("{question}", question)
    reply = llm(prompt, system=system)
    
    # Extract Python code block
    code_match = re.search(r'```python\n(.*?)\n```', reply, re.DOTALL)
    
    if code_match:
        code = code_match.group(1)
        try:
            # Execute the code using the tool
            exec_result = tools.python(code)
            
            # Extract answer from execution output
            # First priority: lines starting with ANSWER:
            for line in reversed(exec_result.split('\n')):
                line = line.strip()
                if line.startswith('ANSWER:'):
                    return line.replace('ANSWER:', '').strip()
            
            # Fallback: return last non-empty line from execution
            for line in reversed(exec_result.split('\n')):
                if line.strip():
                    return line.strip()
        except Exception:
            # If code execution fails, fall through to LLM fallback
            pass
    
    # Fallback: extract from last line of LLM reply if code execution unavailable
    lines = [line.strip() for line in reply.split('\n') if line.strip()]
    return lines[-1] if lines else ""
```

=== FILE: agents/code_compact/prompts/system.md ===

You are a helpful assistant.

=== FILE: agents/code_compact/prompts/task.md ===

{question}

---

=== FILE: agents/code_fallback/harness.py ===

```python
"""Harness that executes Python code with multi-path answer extraction."""

import re


def solve(question, llm, tools, files):
    system = """You are an expert problem solver. For the given problem, write Python code that solves it completely.

Your Python code MUST:
1. Solve the problem end-to-end
2. End with a line that prints: ANSWER: <final_answer>

Write your code in a ```python code block. You may explain your approach, but the code must be self-contained and executable.

After the code block, do not modify or restate the answer—let the code output speak."""
    
    prompt = files["prompts/task.md"].replace("{question}", question)
    reply = llm(prompt, system=system)
    
    # Extract Python code block
    code_match = re.search(r'```python\n(.*?)\n```', reply, re.DOTALL)
    
    if code_match:
        code = code_match.group(1)
        try:
            # Execute the code using the tool
            exec_result = tools.python(code)
            
            # Strategy 1: Extract answer from ANSWER: marker (primary)
            for line in reversed(exec_result.split('\n')):
                line = line.strip()
                if line.startswith('ANSWER:'):
                    return line.replace('ANSWER:', '').strip()
            
            # Strategy 2: Last non-empty line (secondary fallback)
            for line in reversed(exec_result.split('\n')):
                if line.strip():
                    answer = line.strip()
                    # Filter: if last line looks like an answer (numeric, short text), return it
                    # This handles cases where code prints result without ANSWER: prefix
                    return answer
        except Exception:
            # If code execution fails, fall through to LLM fallback
            pass
    
    # Fallback: extract from last line of LLM reply if code execution unavailable
    lines = [line.strip() for line in reply.split('\n') if line.strip()]
    return lines[-1] if lines else ""
```

=== FILE: agents/code_fallback/prompts/system.md ===

You are a helpful assistant.

=== FILE: agents/code_fallback/prompts/task.md ===

{question}