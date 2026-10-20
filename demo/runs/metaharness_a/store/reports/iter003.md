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
