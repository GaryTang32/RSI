# Iteration 2 Report

## Summary
Two candidates tested: **verification_harness** (two-pass verification) and **hybrid_code_text_harness** (reasoning + code).

## Results
- **verification_harness**: 58.3% avg (+16.6Δ from seed), 9219.75 context cost
  - Succeeded on 7/12 tasks (modest improvement over seed's 4/12)
  - Two LLM calls created overhead without consistent error recovery
  - Root cause: Re-verification often confirms incorrect answers rather than catching them
  
- **hybrid_code_text_harness**: **100% (+66.7Δ)**, 3249.0 context cost
  - Perfect on all 12 numeric tasks
  - Mechanism: Separate reasoning (narrative) from code (executable block)
  - Clean regex extraction of code blocks; graceful fallback to ANSWER: marker

## Key Observations
1. **Verification doesn't fix bad reasoning**: If the first pass is wrong, the second pass often confirms the error
2. **Separation of concerns beats enforcement**: Asking for "reasoning + code block" is more effective than strict format rules
3. **Arithmetic via Python is correct**: Code execution handles all digit sums, modular arithmetic, and bit counts perfectly
4. **Single-pass + tool is efficient**: 1 LLM call + 1 tool call is faster than multi-pass reasoning

## Takeaway for Iteration 3
The frontier is now optimal on numeric tasks (100%). Explore: (1) Cost reduction by extracting task-specific context, or (2) Alternative mechanisms (self-critique, staged generation) that might generalize to non-numeric tasks or catch edge cases. Avoid repeating axes F, C, B—try A, E, D instead.

---

## Step 1–3: Hypotheses and Mechanism Design

I have studied the traces and top candidates. Here are two falsifiable hypotheses targeting different mechanisms:

### **Hypothesis 1: Pre-extracted context improves code generation efficiency**
Numeric problems always embed key quantities (factorials, moduli, digit counts). If we extract these quantities and operation types from the problem statement itself, then feed them back as structured context to the model, code generation becomes faster and more accurate. The model doesn't waste tokens re-discovering what the problem is asking.

*Example (evolve-numeric-007: digit sum of 64!):*
- Current hybrid: Full reasoning, then code
- This variant: Extract ["64", "factorial", "digit sum"] from question → include as hints → model generates code faster
- Expected: Same 100% accuracy, but fewer reasoning tokens (efficiency gain)

**Axes**: A (augmented prompt structure) + B (extracted quantities as state)

### **Hypothesis 2: Self-critique before execution catches code generation bugs**
Hybrid's strength is code execution, but occasional syntax or logic errors still occur (e.g., off-by-one, missing imports). If we ask the model to explicitly critique its own generated code *before* execution, it can catch bugs and regenerate, creating a feedback loop. This is especially valuable on complex problems where the first code attempt may have subtle errors.

*Example (evolve-numeric-008: 34462116119303 mod 650):*
- Hybrid generated code and executed (happened to work)
- This variant: Generate code → model critiques for correctness → re-generates if needed → execute
- Expected: Same/better accuracy, but with explicit error recovery

**Axes**: E (critique as update trigger) + F (model-written critique and potential fix)

---

## Step 4: Implementation

```json
{
  "iteration": 3,
  "candidates": [
    {
      "name": "context_extracted_code_harness",
      "base_system": "hybrid_code_text_harness",
      "hypothesis": "Pre-extracting key quantities and operations from the problem statement and including them as structured context improves code generation efficiency and reduces token usage without sacrificing correctness.",
      "axis": "exploitation",
      "components": ["axis:A (augmented prompt)", "axis:B (extracted context state)", "axis:D (resource efficiency)"]
    },
    {
      "name": "self_critique_code_harness",
      "base_system": "hybrid_code_text_harness",
      "hypothesis": "Adding an explicit self-critique step after code generation allows the model to identify and fix bugs before execution, improving robustness on complex problems and reducing error rates on edge cases.",
      "axis": "exploration",
      "components": ["axis:E (critique as update trigger)", "axis:F (model-written critique loop)"]
    }
  ]
}
```

---
