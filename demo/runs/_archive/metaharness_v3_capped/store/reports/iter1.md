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
