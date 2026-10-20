# Iteration 1: Evolution Report

## Step 0: Post-Eval Report
No prior iterations to report (Iteration 0 was baseline only).

---

## Step 1-3: Analysis & Hypotheses

**Current State:**
- Seed baseline: 8.3% accuracy (1/12 correct), ~1526 tokens/task
- All 12 tasks are numeric (sum of digits, modular arithmetic, bit counting)
- Only evolve-numeric-011 succeeded (sum of digits of 13^10 = 67)

**Failure Analysis (from traces):**

1. **Extraction/Format Issues** (~40% of failures):
   - evolve-numeric-002: "-183,764" vs "-183764" (comma formatting)
   - evolve-numeric-006: LaTeX math in last line breaks exact match
   - evolve-numeric-008: Verification text after answer breaks extraction
   
2. **Computational Errors** (~60% of failures):
   - evolve-numeric-000: 41! digit sum = 198 (expected 144) — Python ran but wrong result
   - evolve-numeric-005: 66! digit sum = 261 (expected 351) — off by 90
   - evolve-numeric-004, 007, 009: Digit sums wrong despite tool use
   - evolve-numeric-001, 003, 008: Modular arithmetic wrong

**Root Causes:**
- Model computes without verifying ("write once, submit once")
- Answer extraction is too naive (last line, no format recognition)
- Model isn't guided to self-check or format clearly

---

## Step 4: Prototype & Hypotheses

### **Hypothesis 1:** "Structured solve-verify-answer prompting with explicit answer format will catch both computational errors (via verification step) and extraction errors (via format enforcement)."
- **Mechanism**: Guide LLM through: (1) understand → (2) solve with tools → (3) verify via re-computation → (4) state ANSWER:X on final line
- **Targeting**: Computational errors (verification) + extraction issues (format)
- **Axes**: A (prompt template) + F (model-written verification logic)

### **Hypothesis 2:** "Robust multi-strategy answer extraction that handles formatting variations will recover correct answers even when LLM uses unexpected formats."
- **Mechanism**: Parse LLM output using fallback strategies: (1) "ANSWER:" line → (2) "= number" pattern → (3) bold number → (4) last number, with cleaning (remove commas, extra spaces)
- **Targeting**: Extraction errors only (allows simple prompts)
- **Axes**: C (selection/retrieval algorithm)

**Exploration Mix**: 
- Candidate 1 = Exploitation (improve prompting)
- Candidate 2 = Exploration (different mechanism: extraction algorithm vs. prompting)

---

```json
{
  "iteration": 1,
  "candidates": [
    {
      "name": "solve_verify_answer",
      "base_system": "seed",
      "hypothesis": "Structured prompting requiring explicit verification step and clear ANSWER format will catch computational errors and improve extraction reliability",
      "axis": "exploitation",
      "components": ["axis:A", "axis:F"]
    },
    {
      "name": "smart_extraction",
      "base_system": "seed",
      "hypothesis": "Robust multi-strategy answer extraction handling formatting variations (commas, bold, patterns) will recover correct answers without requiring LLM prompt changes",
      "axis": "exploration",
      "components": ["axis:C"]
    }
  ]
}
```

---

=== FILE: agents/solve_verify_answer/harness.py ===
```python
"""Harness with structured verification and clear answer format."""


def solve(question, llm, tools, files):
    system = files.get("prompts/system.md", "")
    prompt = files["prompts/task.md"].replace("{question}", question)
    reply = llm(prompt, system=system)
    
    # Extract ANSWER: line if present (preferred format)
    lines = reply.strip().splitlines()
    for line in lines:
        if "ANSWER:" in line:
            parts = line.split("ANSWER:", 1)
            if len(parts) == 2:
                value = parts[1].strip()
                # Take only the first token (the numeric value)
                value = value.split()[0] if value else ""
                return value
    
    # Fallback: return last non-empty line
    lines = [line for line in lines if line.strip()]
    return lines[-1] if lines else ""
```

=== FILE: agents/solve_verify_answer/prompts/system.md ===
```
You are a helpful assistant skilled at solving mathematical and computational problems.

Your approach should follow these steps for every problem:
1. UNDERSTAND: State what the problem is asking for.
2. SOLVE: Show your work step-by-step. Use Python for all calculations to ensure accuracy.
3. VERIFY: Double-check your answer by either re-computing using a different method or checking key calculation steps.
4. ANSWER: State your final answer in this exact format on its own line:
   ANSWER: <numeric_value>

The verification step is essential — it catches errors before you respond. Ensure your final ANSWER line contains ONLY the numeric value with no additional text.
```

=== FILE: agents/solve_verify_answer/prompts/task.md ===
```
Solve this problem step-by-step:

{question}

Follow the structure:
1. Understand what is being asked
2. Solve using Python or step-by-step calculation
3. Verify your solution is correct
4. End with: ANSWER: <final_value>
```

---

=== FILE: agents/smart_extraction/harness.py ===
```python
"""Harness with intelligent answer extraction handling multiple format variations."""

import re


def solve(question, llm, tools, files):
    system = files.get("prompts/system.md", "")
    prompt = files["prompts/task.md"].replace("{question}", question)
    reply = llm(prompt, system=system)
    
    answer = extract_answer(reply)
    return answer if answer else ""


def extract_answer(reply):
    """Extract numeric answer from LLM reply, handling various formats."""
    lines = reply.strip().splitlines()
    
    if not lines:
        return ""
    
    # Strategy 1: Look for ANSWER: <value> line
    for line in lines:
        if "ANSWER:" in line:
            match = re.search(r'ANSWER:\s*([-]?[\d,]+(?:\.\d+)?)', line)
            if match:
                value = match.group(1)
                cleaned = clean_numeric(value)
                if cleaned:
                    return cleaned
    
    # Strategy 2: Look for "= <number>" at end of line
    for line in reversed(lines):
        match = re.search(r'=\s*([-]?[\d,]+(?:\.\d+)?)\s*$', line)
        if match:
            value = match.group(1)
            cleaned = clean_numeric(value)
            if cleaned:
                return cleaned
    
    # Strategy 3: Look for bold/emphasis markup around number
    for line in reversed(lines):
        # Match **number** or similar
        match = re.search(r'\*\*\s*([-]?[\d,]+(?:\.\d+)?)\s*\*\*', line)
        if match:
            value = match.group(1)
            cleaned = clean_numeric(value)
            if cleaned:
                return cleaned
    
    # Strategy 4: Extract last number found in last line
    if lines:
        last = lines[-1]
        numbers = re.findall(r'[-]?[\d,]+(?:\.\d+)?', last)
        if numbers:
            value = numbers[-1]
            cleaned = clean_numeric(value)
            if cleaned:
                return cleaned
    
    # Fallback: return last line as-is
    return lines[-1] if lines else ""


def clean_numeric(value):
    """Clean numeric value by removing formatting characters."""
    if not value:
        return ""
    
    # Remove commas (thousands separator)
    value = value.replace(",", "")
    # Remove extra spaces
    value = value.strip()
    
    # Validate it parses as a number
    try:
        if "." in value:
            float(value)
        else:
            int(value)
        return value
    except ValueError:
        return ""
```

=== FILE: agents/smart_extraction/prompts/system.md ===
```
You are a helpful assistant.
```

=== FILE: agents/smart_extraction/prompts/task.md ===
```
{question}

Solve this problem. Feel free to show your work and reasoning. When you provide your final answer, you may format it as ANSWER: <value> or simply state it clearly.
```