Looking at the history, I need to analyze iteration 2's failures and develop two new hypotheses for iteration 3.

## Analysis: Why Iteration 2 Regressed

**Iteration 2 results:**
- `verify_calculations`: 25% (regressed from seed's 33.3%)  
- `structured_numeric_solver`: 16.7% (regressed further)

Reading the traces of the frontier winner (`answer_format_smart_extraction` at 50%):
- **All 6 failures are genuine calculation errors**, not extraction failures
- Example (evolve-numeric-000): Model runs Python correctly (41! = 33452526...), but then **manually sums digits as 169 instead of 144**
- The model has Python output available but ignores it in favor of manual (incorrect) arithmetic

**Root cause:** Iteration 2's attempts (verification emphasis, structured templates) made the prompt heavier/more confusing, causing the model to rely more on narrative reasoning rather than tool outputs. Iteration 1's simpler, focused extraction wins.

## Step 0: Missing Report

=== FILE: reports/iter2.md ===
## Iteration 2: Verification & Structure

**Systems tested:**
- `verify_calculations`: 25.0% (-8.3 from seed), cost 2246
- `structured_numeric_solver`: 16.7% (-16.7 from seed), cost 2163

### Analysis
Both candidates **regressed significantly** from seed (33.3%, cost 2201). Root cause: both added complexity (verification phrases, 5-step templates, longer prompts) that *increased* the model's tendency to use narrative reasoning instead of trusting Python tool outputs.

The frontier winner from iter1 (`answer_format_smart_extraction`, 50%) actually used Python correctly in many traces but then reported wrong arithmetic manually afterward. Iteration 2's emphasis on "verification" and "structured steps" made this worse, not better.

### Key Finding
All remaining failures are **arithmetic errors in narrative**, not code errors:
- Model computes 41! correctly via Python but then manually sums digits as 169 (correct: 144)
- Model runs modular arithmetic code but then writes wrong final answer
- The issue is *reporting* the Python result, not computing it

### Takeaway
To improve beyond 50%, focus on: (1) forcing Python for arithmetic (no manual math), (2) making Python outputs the authoritative answer source, (3) keeping prompts **simple and direct**—less structure beats more.

---

## Step 1–3: Hypotheses & Paper Walkthrough

### Frontier Status
- **Best:** `answer_format_smart_extraction` (50%, cost 3429)
- **Next best:** seed (33.3%, cost 2201)
- **Regressed:** verify_calculations, structured_numeric_solver

### Hypothesis 1 (Exploitation, Axis A): Enforce Python-only arithmetic
**Claim:** By explicitly forbidding manual arithmetic and requiring Python for *every* numeric operation (sums, products, digit counting), the model will report Python outputs instead of hallucinating. Same extraction logic, higher accuracy.

**Paper test on evolve-numeric-000:**
- Current: Model runs Python, gets 144, but then manually re-sums as 169 → extract "169" → fail
- With enforcement: Prompt says "Do NOT manually add. Use Python code: `digit_sum = sum(...)`". Model outputs Python result → extract "144" → success

### Hypothesis 2 (Exploration, Axes A+C): Result-variable pairing
**Claim:** By requiring the model to state answers as "Result: <value>" where value is a Python variable, and extracting from "Result:" pattern first, we decouple narrative from code. Even if the model writes wrong arithmetic in narrative, the code result is extracted.

**Paper test on evolve-numeric-000:**
- Current: Narrative says "= 169", ANSWER: 169 → fail
- With pairing: Prompt asks for "Result: <computed_value>". Model writes:
  ```
  Python computed: digit_sum = 144
  Result: 144
  Narrative explanation: ... (could be wrong) ...
  ANSWER: 169 (wrong narrative)
  ```
- Extraction: Look for "Result:" first → extract 144 → success

Both stay at 1 LLM call, no cost explosion. Hypothesis 1 is lighter (prompt guidance only). Hypothesis 2 is more exploratory (new extraction mechanism + prompt format).

---

```json
{
  "iteration": 3,
  "candidates": [
    {
      "name": "python_enforced_arithmetic",
      "base_system": "answer_format_smart_extraction",
      "hypothesis": "By explicitly requiring Python code for all arithmetic operations (never manual calculation) and removing looser guidance, the model will report Python output directly, eliminating calculation errors that stem from narrative re-derivation.",
      "axis": "exploitation",
      "components": ["axis:A_prompt_enforcement_python_only"]
    },
    {
      "name": "result_variable_extraction",
      "base_system": "answer_format_smart_extraction",
      "hypothesis": "By requiring the model to state answers as 'Result: <value>' where value is a Python variable, and extracting from that pattern first, we decouple narrative reasoning from code output, preventing errors where the model narrates wrong arithmetic after correct Python.",
      "axis": "exploration",
      "components": ["axis:A_result_format", "axis:C_variable_based_extraction"]
    }
  ]
}
```

---

=== FILE: agents/python_enforced_arithmetic/harness.py ===
"""Enforce Python-only arithmetic: require code for all numeric operations."""

import re


def solve(question, llm, tools, files):
    system = files.get("prompts/system.md", "")
    prompt = files["prompts/task.md"].replace("{question}", question)
    reply = llm(prompt, system=system)
    
    return extract_answer(reply)


def extract_answer(text):
    """Extract numeric answer using multiple strategies."""
    
    # Strategy 1: Look for "ANSWER: <value>" pattern (most reliable)
    match = re.search(r'ANSWER\s*:\s*(-?\d+(?:[.,]\d+)*)', text, re.IGNORECASE)
    if match:
        return clean_number(match.group(1))
    
    # Strategy 2: Look for bold numbers like **123** - take the last one
    bold_matches = re.findall(r'\*\*(-?\d+(?:[.,]\d+)*)\*\*', text)
    if bold_matches:
        return clean_number(bold_matches[-1])
    
    # Strategy 3: Look for standalone numbers at the end of lines
    lines = text.strip().split('\n')
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        # Extract number at or near end of line (allow some trailing text)
        match = re.search(r'(-?\d+(?:[.,]\d+)*)\s*(?:\)|\.)?$', line)
        if match:
            return clean_number(match.group(1))
    
    # Fallback: return last non-empty line
    for line in reversed(lines):
        if line.strip():
            return line.strip()
    
    return ""


def clean_number(s):
    """Remove commas and spaces from numeric string."""
    return s.replace(',', '').replace(' ', '')

=== FILE: agents/python_enforced_arithmetic/prompts/system.md ===
You are a helpful assistant that solves numeric problems accurately.

**CRITICAL: For all arithmetic operations (sums, products, factorials, remainders, digit counts, etc.), you MUST use Python code. Do not perform any arithmetic manually or in your head.**

When providing numeric answers:
- Write Python code for every calculation
- Show the code and its output
- Base your final answer on the Python output, never on manual arithmetic
- End with "ANSWER: <value>" where value is the Python result

Show your work by displaying code and output, then provide the final answer clearly.

=== FILE: agents/python_enforced_arithmetic/prompts/task.md ===
{question}

Solve this problem step-by-step. For every numeric operation (sum, product, division, modulo, etc.), write Python code to compute it. Do not calculate by hand.

After your code, provide your final numeric answer clearly as ANSWER: <value>

=== FILE: agents/result_variable_extraction/harness.py ===
"""Result-variable extraction: prioritize 'Result: <value>' pattern from Python variables."""

import re


def solve(question, llm, tools, files):
    system = files.get("prompts/system.md", "")
    prompt = files["prompts/task.md"].replace("{question}", question)
    reply = llm(prompt, system=system)
    
    return extract_answer(reply)


def extract_answer(text):
    """Extract numeric answer, prioritizing Python result statements."""
    
    # Strategy 1: Look for "Result: <value>" pattern (Python variable output)
    match = re.search(r'Result\s*:\s*(-?\d+(?:[.,]\d+)*)', text, re.IGNORECASE)
    if match:
        return clean_number(match.group(1))
    
    # Strategy 2: Look for "ANSWER: <value>" pattern
    match = re.search(r'ANSWER\s*:\s*(-?\d+(?:[.,]\d+)*)', text, re.IGNORECASE)
    if match:
        return clean_number(match.group(1))
    
    # Strategy 3: Look for bold numbers like **123** - take the last one
    bold_matches = re.findall(r'\*\*(-?\d+(?:[.,]\d+)*)\*\*', text)
    if bold_matches:
        return clean_number(bold_matches[-1])
    
    # Strategy 4: Look for standalone numbers at the end of lines
    lines = text.strip().split('\n')
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        # Extract number at or near end of line
        match = re.search(r'(-?\d+(?:[.,]\d+)*)\s*(?:\)|\.)?$', line)
        if match:
            return clean_number(match.group(1))
    
    # Fallback: return last non-empty line
    for line in reversed(lines):
        if line.strip():
            return line.strip()
    
    return ""


def clean_number(s):
    """Remove commas and spaces from numeric string."""
    return s.replace(',', '').replace(' ', '')

=== FILE: agents/result_variable_extraction/prompts/system.md ===
You are a helpful assistant that solves problems accurately using computation tools.

When solving numeric problems:
- Write Python code to perform calculations
- After your code output, state the result in this format: Result: <value>
- The Result value must be the actual output from your Python code, not a manual recalculation
- End with "ANSWER: <value>" for the final answer (which should match Result)

Always trust the Python computation. If your narrative reasoning differs from the Python output, the Python output is correct.

=== FILE: agents/result_variable_extraction/prompts/task.md ===
{question}

Solve this problem step-by-step:
1. Write Python code to compute the answer
2. Show the code output
3. State the result: Result: <computed value>
4. Explain your reasoning
5. State final answer: ANSWER: <value>

The Result value (from step 3) must match your computed Python output exactly.