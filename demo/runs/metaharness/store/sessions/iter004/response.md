# Iteration 4: Prompt Efficiency and Staged Reasoning

## Step 0: Missing Report

=== FILE: reports/iter3.md ===
# Iteration 3: Format-Driven Approaches Miss the Frontier

**Results**:
- `code_json_answer`: 100% accuracy (12/12), 1712.5 tokens → dominated
- `code_decompose`: 100% accuracy (12/12), 1621.7 tokens → dominated

**Why both missed the frontier**:
Both mechanisms achieved perfect accuracy but at higher token cost than frontier leader `code_execution` (1491 tokens).

- `code_json_answer`: System prompt required JSON output format; added ~220 tokens overhead (instruction + model verbosity adapting to JSON requirement) without improving correctness.
- `code_decompose`: Required problem decomposition in `<ANALYSIS>` tags before code; added ~130 tokens overhead for analysis section without correctness gain.

**Key insight**: On tasks already 100% correct, instruction complexity doesn't improve accuracy—it only increases cost. Both candidates treated 100% numeric accuracy as the target, missing that **the frontier is cost-limited, not accuracy-limited**.

**Takeaway for Iter 4**: The mechanism (code execution + output extraction) is saturated. Improvements must come from: (1) prompt efficiency (reduce overhead while preserving mechanism), or (2) genuinely different retrieval/reasoning (prepare for non-numeric generalization). Avoid format/verbosity changes on saturated mechanisms.

---

## Step 1-3: Frontier Analysis & Hypotheses

**Frontier status**: 
- Leader: `code_execution` at (100%, 1491 tokens)
- Dominated: `code_json_answer` (100%, 1712.5), `code_decompose` (100%, 1621.7)
- Weak: `code_compact` (41.7%, 1398.5), `python_answer` (8.3%, 1396.5)

**Opportunity axes**:
- Previous iterations all exploited axis A (prompt). Iter 2 tried minimalism (failed), Iter 3 added structure (added cost).
- Axis E (execution pipeline) and F (model reasoning) remain largely unexplored.
- Axis C (retrieval algorithm) is solved by code_execution's dual fallback (ANSWER: then last line).

**Two falsifiable hypotheses**:

1. **"Minimal explicit prompt" (Axis A)**: Code_compact's failure was due to *ambiguous* terseness (e.g., placeholder `<result>`), not terseness itself. A minimal prompt that is *explicit* about Python-only + code block format + ANSWER: output will reduce token cost without regression.
   - Walkthrough: Model sees "Write Python code; must end: `print(f"ANSWER: {result}")`; no bash/pseudocode." → produces clean code block → saves ~50-100 tokens vs. verbose prompt → executes → extracts "ANSWER: N" → correct ✓

2. **"Staged approach before code" (Axis E+F)**: Requiring model to write a brief approach/pseudocode *before* Python code gives the model space to reason about complexity, reducing errors on edge cases and non-numeric tasks. Single LLM call (approach + code together) pays for thinking without extra calls.
   - Walkthrough: Model outputs:
     ```
     Approach: Sum all digits in 41! by converting to string.
     
     Code: [python block with sum logic]
     ```
     Extraction unchanged → still extracts "144" ✓ Cost minimal (approach is ~50 tokens; avoids model confusion).

---

## Step 4: Implementations

```json
{
  "iteration": 4,
  "candidates": [
    {
      "name": "code_terse_explicit",
      "base_system": "code_execution",
      "hypothesis": "A minimal but explicit system prompt that removes soft language and emphasizes Python-only execution will reduce token overhead by 10-15% while maintaining 100% accuracy, since the mechanism (code execution + output extraction) is invariant to instruction verbosity when properly specified.",
      "axis": "exploitation",
      "components": ["A: minimal system prompt with explicit constraints", "D: no elaboration or examples"]
    },
    {
      "name": "code_approach_first",
      "base_system": "code_execution",
      "hypothesis": "Requiring the model to outline its approach before writing code creates a reasoning checkpoint that reduces errors on complex problems and improves robustness for non-numeric tasks, within a single LLM call (approach section + code section).",
      "axis": "exploration",
      "components": ["E: staged response structure (approach + code)", "F: model-written intermediate reasoning before code execution"]
    }
  ]
}
```

---

=== FILE: agents/code_terse_explicit/harness.py ===
"""Harness with minimal, explicit system prompt."""

import re


def solve(question, llm, tools, files):
    system = """Write Python code to solve this problem. Requirements:
- Code must be in a ```python code block
- Code must end with: print(f"ANSWER: {answer_value}")
- Code must be self-contained and executable
- Do not use bash, pseudocode, or non-Python languages"""
    
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

=== FILE: agents/code_terse_explicit/prompts/system.md ===
You are a helpful assistant.

=== FILE: agents/code_terse_explicit/prompts/task.md ===
{question}

---

=== FILE: agents/code_approach_first/harness.py ===
"""Harness that requires approach statement before code generation."""

import re


def solve(question, llm, tools, files):
    system = """You are an expert problem solver. For any problem, first state your approach, then write Python code.

Format your response as:

Approach: [Brief description of strategy, 1-2 sentences]

Then provide Python code in a ```python code block. Your code must:
1. Solve the problem end-to-end
2. End with: print(f"ANSWER: {final_answer}")
3. Be self-contained and executable

The approach helps validate your logic; the code executes it."""
    
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

=== FILE: agents/code_approach_first/prompts/system.md ===
You are a helpful assistant.

=== FILE: agents/code_approach_first/prompts/task.md ===
{question}