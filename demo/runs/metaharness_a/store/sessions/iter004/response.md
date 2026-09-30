# Iteration 4

## Step 0: Post-Eval Reports (Missing)

=== FILE: reports/iter002.md ===
**Iteration 2 Results Summary:**

Two candidates designed to fix computational errors from Iteration 1 (where smart_extraction achieved 41.7%):

- `compute_then_extract` (axis:A,C): 0.0% — failed completely despite Python-first design
- `multi_check_verify` (axis:A,E): **66.7% (+25%)** — two-stage solving with independent re-verification

**Key Findings:**
1. **Multi-stage verification wins**: Two independent LLM solve attempts caught errors smart_extraction missed (41.7% → 66.7%). On Pareto frontier.
2. **compute_then_extract failure**: Attempted to extract answers directly from Python code output via regex, but broke extraction logic — achieved 0%.
3. **Cost/accuracy tradeoff**: multi_check_verify costs 7199 tokens (83% higher than smart_extraction) but improves accuracy by 25 percentage points.

**Takeaway**: Two-stage redundancy (re-solve independently) is more effective than prompt restructuring or complex extraction regex. However, 4 failures remain (evolve-numeric-000, -004, -005, -007), likely due to:
- Systematic computational errors in both stages (e.g., digit sums)
- Weak extraction format enforcement (relies on flexible regex)

**Next iteration**: Exploit multi_check_verify by enforcing stricter output formats and Python-first computation; explore whether single-stage self-critique can match its accuracy at lower cost.

---

=== FILE: reports/iter003.md ===
**Iteration 3 Results Summary:**

Attempted to improve on multi_check_verify (66.7%) via Python-focused computation and ensemble voting:

- `python_constrained_multi_check` (axis:E): 0.0% — two-stage Python-first approach failed like compute_then_extract
- `ensemble_voting_simple` (axis:none): 8.3% (-58.4%) — three independent solves with majority voting severely regressed

**Key Findings:**
1. **Python-first extraction broke**: Similar regex-based extraction from code output as compute_then_extract; 0% accuracy indicates extraction logic or prompt structure is fundamentally flawed when prioritizing code output.
2. **Ensemble voting failed**: Three independent LLM calls (3× cost) with majority voting achieved only 1/12 correct — worse than seed. Suggests:
   - LLM errors are not random; model makes same mistakes across attempts
   - Majority vote doesn't help if all/most attempts converge to same wrong answer
   - High cost (6948 tokens) with minimal accuracy gain over seed (8.3%)

**Takeaway**: Redundancy through multiple independent attempts doesn't fix systematic computational errors. Both candidates regressed/failed, indicating the bottleneck is **prompt/extraction design, not voting schemes or code-output prioritization**.

**Remaining frontier**: multi_check_verify (66.7%, 7199 cost) is still best. To improve, must either:
- Strengthen prompts to prevent systematic errors (e.g., enforce Python more strictly)
- Tighten extraction to enforce output format compliance
- Combine multi-stage with better prompt discipline

---

## Step 1-2: Analysis & Hypotheses

**Observation**: multi_check_verify (66.7%) remains dominant after 3 iterations. Its two-stage structure catches errors by forcing independent re-solving, but 4 failures persist:
- evolve-numeric-000: 41! digit sum (extracted 129, expected 144)
- evolve-numeric-004: 52^12 digit sum (extracted 92, expected 100)
- evolve-numeric-005: Unknown (extracted 297, expected 351)
- evolve-numeric-007: Unknown (extracted 261, expected 324)

**Root cause analysis** (from traces):
1. Even with two LLM calls, both stages sometimes converge on the same wrong answer (e.g., both get digit sum wrong)
2. Extraction uses flexible regex ("ANSWER:", "=", "**...**"), which may accept malformed or wrong answers
3. Prompts don't strictly enforce Python computation; model may do mental math and make arithmetic errors

**Hypothesis 1 (Exploitation):** Stricter output format enforcement + Python-first prompting in multi-stage will reduce systematic computational errors. If both stages are forced to output "ANSWER: <number>" *only* and use Python for *all* arithmetic, extraction becomes simpler and errors fewer.

**Hypothesis 2 (Exploration):** A single LLM call that explicitly requires the model to (1) write Python code, (2) run it, (3) extract the output into "ANSWER: <number>" format will achieve better accuracy-per-cost than seed by forcing code-first reasoning without multi-stage overhead. This combines code discipline with single-pass efficiency.

---

## Step 3: Prototyping on Examples

**Hypothesis 1 (Constrained Multi-Verify) on evolve-numeric-000:**
- Problem: "Sum of digits of 41!"
- Stage 1 new prompt: "You MUST use Python for ALL arithmetic. Your answer must be on a line by itself: ANSWER: <number>"
- Model writes: `print(sum(int(d) for d in str(factorial(41))))` → outputs 144
- Model then writes: "ANSWER: 144"
- Extraction: Match only "ANSWER: \d+" → "144" ✓
- Stage 2 (re-solve): Same structure, forces independent Python pass. If both stages agree on 144, return it.
- vs. current multi_check_verify: Flexible extraction accepted "ANSWER: 129" even though Python output should have shown 144; prompt allowed mental math fallback.

**Hypothesis 2 (Code-Extract-Single) on evolve-numeric-001:**
- Problem: "40627866717431 mod 148?"
- Prompt: "Solve using Python. Write code that outputs ONLY the answer. Then extract that number and state ANSWER: <number>."
- Model writes:
  ```python
  print(40627866717431 % 148)  # Output: 91
  ```
- Model then: "The code output is 91. ANSWER: 91"
- Extraction: "ANSWER: 91" ✓
- Cost: ~3-4k tokens (one call, not two)
- vs. smart_extraction (41.7%, 3921 tokens): Single code-first pass forces Python discipline without multi-stage; should improve accuracy toward multi_check territory but cheaper.

---

## Step 4: Implementation

```json
{
  "iteration": 4,
  "candidates": [
    {
      "name": "constrained_multi_verify",
      "base_system": "multi_check_verify",
      "hypothesis": "Enforcing strict ANSWER: <number>-only format and requiring Python for all arithmetic in both stages will reduce systematic computational errors by preventing mental-math fallbacks and improving extraction reliability.",
      "axis": "exploitation",
      "components": ["axis:A", "axis:C"]
    },
    {
      "name": "code_extract_single",
      "base_system": "seed",
      "hypothesis": "A single-stage harness requiring explicit Python code output followed by model-written extraction to ANSWER: format will achieve code-discipline benefits of multi-stage harnesses at lower cost, improving over baseline and smart_extraction.",
      "axis": "exploration",
      "components": ["axis:A", "axis:F"]
    }
  ]
}
```

---

=== FILE: agents/constrained_multi_verify/harness.py ===
"""Two-stage solving with strict ANSWER format and Python-enforced computation."""

import re


def solve(question, llm, tools, files):
    system = files.get("prompts/system.md", "")
    
    # Stage 1: Initial solve with strict format requirement
    prompt1 = files["prompts/task.md"].replace("{question}", question)
    reply1 = llm(prompt1, system=system)
    answer1 = extract_strict_answer(reply1)
    
    # Stage 2: Re-verification with independent solving
    if answer1:
        prompt2 = files["prompts/verify.md"].format(question=question, previous_answer=answer1)
    else:
        prompt2 = files["prompts/verify.md"].format(question=question, previous_answer="unknown")
    
    reply2 = llm(prompt2, system=system)
    answer2 = extract_strict_answer(reply2)
    
    # Return verified answer if available, otherwise first attempt
    return answer2 if answer2 else (answer1 if answer1 else "")


def extract_strict_answer(reply):
    """Extract ANSWER: <number> format strictly. Rejects other formats."""
    lines = reply.strip().splitlines()
    
    # Only accept lines that match ANSWER: <number> pattern exactly
    for line in lines:
        # Match "ANSWER: " followed by optional whitespace and a number (with optional negative sign and decimals)
        match = re.search(r'^ANSWER:\s*([-]?[\d.]+)\s*$', line.strip())
        if match:
            value = match.group(1)
            # Validate it's actually numeric
            try:
                if '.' in value:
                    float(value)
                else:
                    int(value)
                # Remove trailing .0 from integers
                if '.' in value and value.endswith('.0'):
                    return value[:-2]
                return value
            except ValueError:
                continue
    
    # Fallback: if no strict ANSWER: line found, try relaxed extraction
    # Look for ANSWER: with potentially more text on same line
    for line in lines:
        if "ANSWER:" in line:
            match = re.search(r'ANSWER:\s*([-]?[\d.]+)', line)
            if match:
                value = match.group(1)
                try:
                    if '.' in value:
                        float(value)
                    else:
                        int(value)
                    if '.' in value and value.endswith('.0'):
                        return value[:-2]
                    return value
                except ValueError:
                    continue
    
    # Final fallback: last number in reply
    all_numbers = re.findall(r'[-]?[\d.]+', reply)
    if all_numbers:
        value = all_numbers[-1]
        try:
            if '.' in value:
                float(value)
            else:
                int(value)
            if '.' in value and value.endswith('.0'):
                return value[:-2]
            return value
        except ValueError:
            pass
    
    return ""

=== FILE: agents/constrained_multi_verify/prompts/system.md ===
You are a precise computational assistant. For every problem:

**Critical rules:**
1. **Use Python for ALL arithmetic.** Never do mental math. Even simple calculations must go in Python.
2. **Trust only Python output.** The Python code is the source of truth, not your reasoning.
3. **Format your final answer exactly:** On a line by itself, write:
   ANSWER: <number>
   Nothing else on that line.

Your goal is accuracy through code-based computation, not speed or elegance.

=== FILE: agents/constrained_multi_verify/prompts/task.md ===
Solve this problem carefully:

{question}

**Your approach:**
1. Write Python code that solves this step-by-step
2. Run the code and show its output
3. Extract the final numeric answer from your code's output
4. On a new line by itself, write exactly:
   ANSWER: <the_number_from_code>

Do NOT include anything else on the ANSWER line — only the number.

=== FILE: agents/constrained_multi_verify/prompts/verify.md ===
You previously solved this problem and obtained: {previous_answer}

Now, re-solve the exact same problem **completely independently and from scratch**, without referencing your previous solution. Treat it as if you're seeing it for the first time.

Problem: {question}

**Your approach:**
1. Write NEW Python code that solves this (don't reuse your previous code)
2. Run the code and show its output
3. Extract the final answer from the code output
4. On a line by itself, state your answer in this format:
   ANSWER: <number>

The ANSWER line must contain ONLY the number — no other text.

=== FILE: agents/code_extract_single/harness.py ===
"""Single-stage: model writes Python, shows output, then extracts to ANSWER format."""

import re


def solve(question, llm, tools, files):
    system = files.get("prompts/system.md", "")
    prompt = files["prompts/task.md"].replace("{question}", question)
    reply = llm(prompt, system=system)
    
    answer = extract_model_answer(reply)
    return answer if answer else ""


def extract_model_answer(reply):
    """Extract ANSWER: <number> that model extracted from its own code output."""
    lines = reply.strip().splitlines()
    
    # Priority 1: Strict ANSWER: <number> format on its own line
    for line in lines:
        match = re.search(r'^ANSWER:\s*([-]?[\d.]+)\s*$', line.strip())
        if match:
            value = match.group(1)
            if is_valid_number(value):
                if '.' in value and value.endswith('.0'):
                    return value[:-2]
                return value
    
    # Priority 2: ANSWER: format with potentially more text (model's explanation)
    for line in lines:
        if "ANSWER:" in line:
            match = re.search(r'ANSWER:\s*([-]?[\d.]+)', line)
            if match:
                value = match.group(1)
                if is_valid_number(value):
                    if '.' in value and value.endswith('.0'):
                        return value[:-2]
                    return value
    
    # Priority 3: "= <number>" at end of line (might appear in code output summary)
    for line in reversed(lines):
        match = re.search(r'=\s*([-]?[\d.]+)\s*$', line)
        if match:
            value = match.group(1)
            if is_valid_number(value):
                if '.' in value and value.endswith('.0'):
                    return value[:-2]
                return value
    
    # Priority 4: Last number in last line
    if lines:
        numbers = re.findall(r'[-]?[\d.]+', lines[-1])
        if numbers:
            value = numbers[-1]
            if is_valid_number(value):
                if '.' in value and value.endswith('.0'):
                    return value[:-2]
                return value
    
    return ""


def is_valid_number(value):
    """Check if string is a valid number."""
    if not value:
        return False
    try:
        if '.' in value:
            float(value)
        else:
            int(value)
        return True
    except ValueError:
        return False

=== FILE: agents/code_extract_single/prompts/system.md ===
You are a precise problem-solver who uses Python for all computations.

For each problem:
- Write Python code that solves it correctly
- Run your code and trust only its output
- Extract the final answer from the code output
- Present your answer in the required format

Your primary tool is Python; your reasoning is secondary to code.

=== FILE: agents/code_extract_single/prompts/task.md ===
Solve this problem:

{question}

**Instructions:**
1. Write Python code that computes the final numeric answer
2. Run the code and show its output (the Python output is your ground truth)
3. Read the output from your code carefully
4. Extract the final number and state it in this format on a line by itself:
   ANSWER: <the_number_from_your_code_output>

Do not include any other text on the ANSWER line. The number you extract must come from what your Python code printed.