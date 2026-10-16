# Iteration 3 Analysis & Candidates

## Step 0: Post-Eval Reports

=== FILE: reports/iter1.md ===
# Iteration 1: Code Execution Breakthrough

**Results**:
- `code_execution`: 100% accuracy (12/12), 1491 avg tokens → **FRONTIER BEST**
- `python_answer`: 8.3% accuracy (1/12), 1396.5 avg tokens → on frontier by cost only

**Key Finding**: Extracting answers directly from Python tool execution (not LLM text) achieved perfect accuracy. `python_answer` failed because despite system prompt requiring Python, the LLM still made arithmetic errors in its text output and didn't reliably emit the PYTHON_RESULT: marker.

**Takeaway**: The mechanism that matters is **who computes** (Python tool vs. LLM brain). Prompt verbosity (axis A) is secondary. Next iteration should either (1) exploit: reduce cost while maintaining 100%, or (2) explore: improve robustness for non-numeric tasks where code output format may vary.

---

=== FILE: reports/iter2.md ===
# Iteration 2: Minimalism Fails; Redundancy Stays

**Results**:
- `code_compact`: 41.7% accuracy (5/12), 1398.5 tokens → on frontier by cost, but broken
- `code_fallback`: 100% accuracy (12/12), 1491 tokens → ties frontier, no improvement

**code_compact Regression**: Minimal system prompt ("Write Python code to solve this...") broke the mechanism. Model stopped generating clean ```python blocks; instead generated embedded bash calls. Axis A (prompt) proved critical after all—too-terse instructions cause generation failures.

**code_fallback No Gain**: Dual-path extraction (ANSWER: marker + last line) couldn't improve over single-path since execution already 100%. Both pathways hit ANSWER: marker consistently.

**Takeaway**: `code_execution` is locally optimal on numeric tasks. The frontier gap is now: **generalization to non-numeric tasks**. Since domain specifies tests on dates, text, lists, number theory (all still computational), improvements must strengthen robustness without sacrificing accuracy. Avoid axis A tweaks; focus on mechanism (C, E, F, B).

---

## Step 1: Frontier & Traces Review

**Current frontier** (from `frontier_val.json`):
- `code_execution` / `code_fallback`: 100% (1491 tokens) — **Pareto best by score**
- `code_compact`: 41.7% (1398.5 tokens) — on frontier by cost but broken
- `python_answer`: 8.3% (1396.5 tokens) — frontier tail

**Critical observation** from traces (e.g., `evolve-numeric-000.jsonl`):
- `code_execution`: Extracts "Sum of digits: 144" correctly from execution output
- `code_compact`: Same code generates "ANSWER: 187" (wrong—LLM error, not tool error)

The model's system prompt matters. Iter 2 showed terse prompts break code generation itself.

---

## Step 2-3: Hypotheses & Mechanism Prototyping

### **Hypothesis 1** (Axis C+D: Structured Answer Extraction)
**Claim**: Requiring code to output answers in a **machine-parseable format** (JSON) rather than relying on text markers will improve robustness on non-numeric tasks where code output may be complex (lists, nested structures, date ranges).

**Walkthrough** (numeric example from traces):
- **Current** (code_execution): Model writes `print(f"Sum of digits: {digit_sum}")` → tool outputs "Sum of digits: 144" → regex scans for ANSWER: (not present) → falls back to last line → extracts "Sum of digits: 144" ✓ (happens to work)
- **New** (code_json): Model writes `print(json.dumps({"answer": digit_sum}))` → tool outputs `{"answer": 144}` → parse JSON, extract key "answer" → returns "144" ✓ (explicit, no fallback needed)

**On non-numeric** (hypothetical date task: "list all dates in Jan 2020"):
- **Current**: Code prints `["2020-01-01", "2020-01-02", ...]` → extract last line → gets list string ✓ (works by accident)
- **New**: Code prints `{"answer": ["2020-01-01", ...]}` → parse JSON, extract "answer" key → ensures consistency across task types

**Axis tags**: C (new JSON retrieval path vs. regex), D (enforce structured answer format)

---

### **Hypothesis 2** (Axis B+E+F: Problem Decomposition as State)
**Claim**: Requiring the LLM to first **decompose the problem** (problem type, approach, edge cases) before writing code will improve correctness on complex/non-numeric tasks. The decomposition serves as **harness-level memory** (axis B) that could later enable multi-step reasoning or debugging.

**Walkthrough** (numeric example from traces):
- **Current** (code_execution): LLM sees "Morgan's ledger: start 49249, add 84718..." → writes code directly → correct ✓
- **New** (code_decompose): LLM outputs:
  ```
  <ANALYSIS>
  problem_type: multi-step_arithmetic
  approach: accumulate_sequential_operations
  edge_cases: large_products, sign_handling
  </ANALYSIS>
  ```
  Then writes same code → correct ✓ (analysis stored as harness memory, makes debugging easier)

**On non-numeric** (date extraction: "Extract all dates between June 2020 and Sept 2026"):
- **Current**: LLM writes code directly; if approach is wrong (forgets end-date filter), bad output
- **New**: LLM must first write:
  ```
  <ANALYSIS>
  problem_type: date_range_filtering
  approach: parse_all_dates, filter_by_range, format_output
  edge_cases: inclusive_vs_exclusive_bounds, leap_years, timezone_handling
  </ANALYSIS>
  ```
  This forces thinking through requirements before coding. If analysis is incomplete, code is more likely to be too. Single LLM call (analysis + code in one reply).

**Axis tags**: B (decomposition plan stored as memory), E (analysis as triggering stage before execution), F (model-written structured reasoning)

---

## Step 4: Implementation

```json
{
  "iteration": 3,
  "candidates": [
    {
      "name": "code_json_answer",
      "base_system": "code_execution",
      "hypothesis": "Requiring code to output answers in JSON format will improve answer extraction robustness across task types by making output structure explicit and machine-parseable, eliminating ambiguity in text-based fallbacks.",
      "axis": "exploitation",
      "components": ["C: JSON parsing as primary retrieval", "D: structured answer format requirement", "F: model writes json.dumps() output"]
    },
    {
      "name": "code_decompose",
      "base_system": "code_execution",
      "hypothesis": "Requiring the LLM to first decompose the problem (type, approach, edge cases) before writing code will improve correctness on complex and non-numeric tasks by forcing explicit reasoning about requirements, and serves as reusable memory for debugging.",
      "axis": "exploration",
      "components": ["B: problem decomposition stored as harness state", "E: staged execution (analysis → code → run)", "F: model writes structured analysis before code"]
    }
  ]
}
```

---

=== FILE: agents/code_json_answer/harness.py ===
"""Harness that executes Python code and extracts answers from JSON-formatted output."""

import re
import json


def solve(question, llm, tools, files):
    system = """You are an expert problem solver. For the given problem, write Python code that solves it completely.

Your Python code MUST:
1. Solve the problem end-to-end
2. Output the final answer as JSON: print(json.dumps({"answer": <final_answer>}))
3. Be self-contained and executable

Write your code in a ```python code block. You may explain your approach before the code, but focus on producing correct output."""
    
    prompt = files["prompts/task.md"].replace("{question}", question)
    reply = llm(prompt, system=system)
    
    # Extract Python code block
    code_match = re.search(r'```python\n(.*?)\n```', reply, re.DOTALL)
    
    if code_match:
        code = code_match.group(1)
        try:
            # Execute the code using the tool
            exec_result = tools.python(code)
            
            # Strategy 1: Try to parse JSON from output
            for line in reversed(exec_result.split('\n')):
                line = line.strip()
                if line.startswith('{'):
                    try:
                        data = json.loads(line)
                        if 'answer' in data:
                            # Found JSON with answer key
                            answer = data['answer']
                            return str(answer) if answer is not None else ""
                    except (json.JSONDecodeError, TypeError):
                        pass
            
            # Strategy 2: Fall back to ANSWER: marker (for robustness on legacy/unformatted output)
            for line in reversed(exec_result.split('\n')):
                line = line.strip()
                if line.startswith('ANSWER:'):
                    return line.replace('ANSWER:', '').strip()
            
            # Strategy 3: Last non-empty line (catch-all)
            for line in reversed(exec_result.split('\n')):
                if line.strip():
                    return line.strip()
        except Exception:
            # If code execution fails, fall through to LLM fallback
            pass
    
    # Fallback: extract from last line of LLM reply
    lines = [line.strip() for line in reply.split('\n') if line.strip()]
    return lines[-1] if lines else ""

=== FILE: agents/code_json_answer/prompts/system.md ===
You are a helpful assistant.

=== FILE: agents/code_json_answer/prompts/task.md ===
{question}

---

=== FILE: agents/code_decompose/harness.py ===
"""Harness that decomposes the problem before generating code."""

import re


def solve(question, llm, tools, files):
    system = """You are an expert problem solver. For any problem, follow this process:

1. Analyze and decompose the problem:
   - Identify the problem type (numeric, date, list, text, logic, etc.)
   - Describe your solution approach
   - List potential edge cases or pitfalls

2. Then write Python code that solves it:
   - End with a line that prints: ANSWER: <final_answer>
   - Be self-contained and executable

Format your response as:

<ANALYSIS>
problem_type: [type]
approach: [your strategy in 1-2 sentences]
edge_cases: [list potential issues]
</ANALYSIS>

Then provide your Python code in a ```python code block."""
    
    prompt = files["prompts/task.md"].replace("{question}", question)
    reply = llm(prompt, system=system)
    
    # Extract and store analysis (for harness-level memory/debugging)
    analysis_match = re.search(r'<ANALYSIS>(.*?)</ANALYSIS>', reply, re.DOTALL)
    analysis = ""
    if analysis_match:
        analysis = analysis_match.group(1).strip()
    
    # Extract Python code block
    code_match = re.search(r'```python\n(.*?)\n```', reply, re.DOTALL)
    
    if code_match:
        code = code_match.group(1)
        try:
            # Execute the code using the tool
            exec_result = tools.python(code)
            
            # Extract answer from execution output
            # First priority: ANSWER: marker
            for line in reversed(exec_result.split('\n')):
                line = line.strip()
                if line.startswith('ANSWER:'):
                    return line.replace('ANSWER:', '').strip()
            
            # Fallback: last non-empty line
            for line in reversed(exec_result.split('\n')):
                if line.strip():
                    return line.strip()
        except Exception:
            # If code execution fails, fall through to LLM fallback
            pass
    
    # Fallback: extract from last line of LLM reply
    lines = [line.strip() for line in reply.split('\n') if line.strip()]
    return lines[-1] if lines else ""

=== FILE: agents/code_decompose/prompts/system.md ===
You are a helpful assistant.

=== FILE: agents/code_decompose/prompts/task.md ===
{question}