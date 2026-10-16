# Iteration 2: Evolution Loop

Looking at the history, I'll first write the missing post-eval report for iteration 1, then propose two new candidates targeting the calculation errors that remain.

## Step 0: Post-Eval Report

=== FILE: reports/iter1.md ===
## Iteration 1: Extraction & Format

**Systems tested:**
- `answer_format_smart_extraction`: 50.0% (+16.7), cost 3429
- `iterative_format_refinement`: 33.3% (-16.7), cost 4161

### Analysis
`answer_format_smart_extraction` reached Pareto frontier on score by introducing regex-based extraction (ANSWER:, bold **, line-ending numbers) and clearer prompt guidance. Improved on 2 additional tasks (-002, -006, -010) over seed.

`iterative_format_refinement` performed worse: retry logic never triggered (extraction always found a number on first try), so the hypothesis was invalid. Cost doubled with no benefit.

### Failure Patterns (answer_format_smart_extraction)
Remaining 6 failures (-000, -003, -004, -005, -007, -009) are NOT extraction failures—model extracted its claimed answer cleanly. Root cause: **calculation errors**. Model computed wrong numeric results (wrong sum of digits, wrong modular arithmetic, etc.) despite showing work.

### Takeaway
Extraction/formatting is largely solved (50% success). Next bottleneck is calculation *correctness*. Need to focus on: (1) forcing tool usage for all math, (2) prompting verification, (3) decomposing problems into checkable steps. Retry logic should trigger on *calculation correctness*, not extraction failure.

---

## Step 1 & 2: Analysis & Hypotheses

### Frontier Status
- **Best score:** answer_format_smart_extraction (50%, cost 3429)
- **Best cost:** seed (33.3%, cost 2201)
- **Pareto frontier:** Both kept; higher-score candidate is best overall

### Root Cause of Remaining Failures
All 6 failures in `answer_format_smart_extraction` are **wrong numeric answers**, not formatting issues. The model computed 169 instead of 144 for a digit sum, 416 instead of 56 for a modular result, etc. Extraction worked; the *answer was wrong*.

### Hypothesis 1 (Exploitation, Axes A+F)
"By refining prompts to *require* Python verification of answers and prompting the model to state 'I verified: X' before the final answer, the model will catch its own errors without needing multiple LLM calls. Extraction will prioritize verified answers, improving correctness."
- **Axis:** A (prompt guidance), F (model-written verification notes)
- **Mechanism:** Single LLM call, but with explicit verification step in prompt; extract from verified sections

### Hypothesis 2 (Exploration, Axes A+C+B)
"By structuring numeric problems into a strict pipeline (parse → identify operations → write/run Python → verify step-by-step → final answer) and extracting only from the final answer section, we reduce free-form reasoning errors. Storage of intermediate steps makes calculation transparent and checkable."
- **Axis:** A (structured prompt template), C (extraction from step-labeled sections), B (explicit step storage in reasoning)
- **Mechanism:** New retrieval strategy: look for step N patterns, extract final answer only from "Final Answer" section

---

## Step 3: Paper Walkthrough

### Example: Task -000 (41! digit sum, expected 144)
Current answer_format_smart_extraction failed by extracting "169".

**Hypothesis 1 (verify_calculations):**
- Prompt: "Calculate 41!. Then VERIFY: run Python to check the digit sum. State 'Verified: <N>' before final answer."
- Model might: "41! = ... [Python run] ... I get 169. Let me verify: [Python recount] ... Verified: 169"
- OR catch error: "I calculated 169, but let me verify... [Python] Verified: 144"
- Extraction: prefer "Verified:" pattern
- Same cost (1 LLM call), potential correctness gain from verification instruction

**Hypothesis 2 (structured_solver):**
- Prompt: "Solve in steps: \n Step 1. Parse: {problem} \n Step 2. Approach: {describe calculation} \n Step 3. Code: {write Python} \n Step 4. Verify: {check result} \n Step 5. Final Answer: {state answer}"
- Model computes: "Step 1: Sum digits of 41! \n Step 2: Compute factorial, convert to string, sum \n Step 3: [Python code] \n Step 4: Double-check ... \n Step 5: Final Answer: 144"
- Extraction: look for "Final Answer:" section
- Different mechanism (structure + step-based extraction vs. free-form + suffix extraction)

Both should stay at ~1 LLM call per task, no cost explosion. Hypothesis 1 is lighter; Hypothesis 2 is more exploratory.

---

## Step 4: Implementation

```json
{
  "iteration": 2,
  "candidates": [
    {
      "name": "verify_calculations",
      "base_system": "answer_format_smart_extraction",
      "hypothesis": "By prompting the model to explicitly verify numeric answers (via Python or recalculation) and marking verified results, extraction can prioritize verified answers, improving correctness on calculation-heavy tasks without additional LLM calls.",
      "axis": "exploitation",
      "components": ["axis:A_prompt_emphasis_verification", "axis:F_model_written_verification"]
    },
    {
      "name": "structured_numeric_solver",
      "base_system": "seed",
      "hypothesis": "By requiring strict step-by-step structure (parse → approach → code → verify → final answer) and extracting only from the Final Answer section, we reduce reasoning errors and make calculations checkable, improving accuracy on numeric tasks.",
      "axis": "exploration",
      "components": ["axis:A_structured_template", "axis:C_step_based_extraction", "axis:B_explicit_intermediate_steps"]
    }
  ]
}
```

---

=== FILE: agents/verify_calculations/harness.py ===
"""Verification-focused harness: prompts model to verify answers, prioritizes verified results."""

import re


def solve(question, llm, tools, files):
    system = files.get("prompts/system.md", "")
    prompt = files["prompts/task.md"].replace("{question}", question)
    reply = llm(prompt, system=system)
    
    return extract_answer(reply)


def extract_answer(text):
    """Extract numeric answer, prioritizing verified results."""
    
    # Strategy 1: Look for "Verified: <value>" or "VERIFIED: <value>" (most reliable)
    match = re.search(r'[Vv]erified\s*:\s*(-?\d+(?:[.,]\d+)*)', text)
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

=== FILE: agents/verify_calculations/prompts/system.md ===
You are a helpful assistant that solves problems accurately and verifies your work.

For numeric problems:
- Show your reasoning step-by-step
- Use Python code for calculations
- ALWAYS verify your numeric answer before stating it
- State verification results clearly: "Verified: <value>" before giving your final answer
- Double-check calculations, especially with tools

Your goal is to provide correct answers. Verification is part of accuracy.

=== FILE: agents/verify_calculations/prompts/task.md ===
{question}

Solve this problem step-by-step and show all work.

For numeric problems: After you calculate an answer, VERIFY it is correct (re-run the calculation, use a different method, or double-check). Then state:

Verified: <your answer>

ANSWER: <your answer>

---

=== FILE: agents/structured_numeric_solver/harness.py ===
"""Structured solver: requires step-by-step numeric reasoning with explicit final answer section."""

import re


def solve(question, llm, tools, files):
    system = files.get("prompts/system.md", "")
    prompt = files["prompts/task.md"].replace("{question}", question)
    reply = llm(prompt, system=system)
    
    return extract_answer(reply)


def extract_answer(text):
    """Extract numeric answer, prioritizing structured 'Final Answer:' section."""
    
    # Strategy 1: Look for "Final Answer:" section (most reliable with structured prompts)
    match = re.search(r'[Ff]inal\s+[Aa]nswer\s*:\s*(-?\d+(?:[.,]\d+)*)', text)
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
    
    # Strategy 4: Line-ending numbers
    lines = text.strip().split('\n')
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        match = re.search(r'(-?\d+(?:[.,]\d+)*)\s*(?:\)|\.)?$', line)
        if match:
            return clean_number(match.group(1))
    
    # Fallback: last non-empty line
    for line in reversed(lines):
        if line.strip():
            return line.strip()
    
    return ""


def clean_number(s):
    """Remove commas and spaces from numeric string."""
    return s.replace(',', '').replace(' ', '')

=== FILE: agents/structured_numeric_solver/prompts/system.md ===
You are a careful problem solver. You solve problems systematically using a structured approach.

For every problem, follow this exact structure:
1. **Parse:** Understand what is being asked
2. **Approach:** Describe your method
3. **Execute:** Write Python code to compute (don't guess)
4. **Verify:** Check your result makes sense
5. **Final Answer:** State the numeric result

This structure ensures accuracy. Do not skip verification.

=== FILE: agents/structured_numeric_solver/prompts/task.md ===
{question}

Solve using this structure:

## Step 1: Parse
(What exactly are we solving for?)

## Step 2: Approach
(How will you solve it? What calculation or method?)

## Step 3: Execute
(Write and run Python code. Show the computation.)

## Step 4: Verify
(Does the result make sense? Double-check if possible.)

## Step 5: Final Answer
(State the numeric answer clearly.)

For numeric answers, always use Python code to compute; do not rely on mental math.