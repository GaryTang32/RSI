# Iteration 1 Report

## Summary
Two candidates tested: **code_gen_harness** (python delegation) and **strict_answer_format_harness** (explicit format).

## Results
- **strict_answer_format_harness**: 41.7% avg (+8.4Δ over seed), 4013.67 context cost
  - **Succeeded** on modular arithmetic tasks (evolve-numeric-001, 003, 006, 008) with correct reasoning chains
  - **Failed** on digit-sum/bit-counting (evolve-numeric-000, 004, 005, 007, 009, 010) despite format compliance
  - Root cause: Explicit ANSWER: marker improved extraction reliability, but arithmetic is still wrong in text

- **code_gen_harness**: 25.0% avg (-8.3Δ), 1430.08 context cost
  - Intended to delegate arithmetic to Python execution (sound hypothesis)
  - **Failed catastrophically**: Model mixed narrative text with code blocks; regex extraction caught syntax errors on 9/12 tasks
  - Only 3/12 passed; 1 correct (evolve-numeric-002) when code extraction worked perfectly

## Key Observations
1. **Format constraints don't fix arithmetic**: Both candidates produce wrong *answers*, not wrong *formats*
2. **Code execution works when extraction succeeds**: evolve-numeric-002 shows perfect correctness via Python
3. **Separation of concerns missing**: code_gen's failure was extraction fragility, not the execution concept

## Takeaway for Iteration 2
Explore two orthogonal fixes: **(1) verification/re-checking to catch arithmetic errors** and **(2) cleaner code-text separation to improve extraction robustness**. Don't just tune parameters; change the mechanism.

---

## Step 1–3: Hypotheses and Mechanism Walkthrough

### Hypothesis 1: **Verification breaks the arithmetic error cycle**
Models make computational mistakes but can often self-correct under pressure to re-verify. If we store the tentative answer and ask for independent recalculation, the second pass may catch errors.

*Example (evolve-numeric-007: digit sum of 64!):*
- Pass 1: Model outputs "ANSWER: 75" (wrong; should be 324)
- Pass 2: Prompt says "I got 75. Please verify independently." → Model recalculates, notices the error, outputs "ANSWER: 324"
- Return: 324 ✓

**Axis**: B (memory: tentative answer) + E (learning trigger: verification call)

### Hypothesis 2: **Explicit code-text separation improves extraction and execution**
The code_gen harness failed because the model's reasoning mixed with code blocks broke regex extraction. If we ask for reasoning *then* a dedicated code block with clear structure, we get cleaner separation and fewer extraction errors.

*Example (evolve-numeric-000: digit sum of 41!):*
- Prompt includes: "After reasoning, provide Python code in ```python ... ``` that outputs ONLY the numerical answer"
- Model response: `[reasoning] ... [code block with clean Python] ...`
- Extract last code block: `import math; …; print(digit_sum)`
- Execute: output `"144"` → Return: 144 ✓

**Axis**: F (model-driven tool usage) + C (retrieval: code extraction algorithm)

---

## Step 4: Implementation

```json
{"iteration": 2, "candidates": [{"name": "verification_harness", "base_system": "strict_answer_format_harness", "hypothesis": "Re-checking arithmetic by asking for independent verification triggers recalculation and catches computational errors; a second LLM call with explicit verification request will catch mistakes missed in the first pass.", "axis": "exploitation", "components": ["axis:B (memory/state: tentative answer)", "axis:E (learning/update trigger: verification call)"]}, {"name": "hybrid_code_text_harness", "base_system": "seed", "hypothesis": "Separating step-by-step reasoning from executable Python code, with explicit structural instructions, improves code extraction reliability and ensures computation is delegated safely to Python rather than attempted in text.", "axis": "exploration", "components": ["axis:F (model-driven tool usage)", "axis:C (selection/retrieval: code extraction algorithm)"]}]}
```
