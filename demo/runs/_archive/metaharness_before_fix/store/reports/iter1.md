# Iteration 1: Guided vs. Multi-Method Report

**What changed**: Two candidates tested multi-turn prompting (vs. seed's single call):
- **guided_format**: Calculate → Verify (2 turns, 4.35k tokens avg)
- **multi_method**: Calculate Method A → Calculate Method B → Compare (3 turns, 11.6k tokens avg)

**Score & cost**: 
- multi_method: **25.0%** (3/12 correct); seed improved to 16.7% and 8.3% remains on frontier
- guided_format: **16.7%** (2/12 correct; 4.35k tokens)
- Pareto: multi_method dominates guided_format (higher score, slightly higher cost is traded for 8.3% gain)

**Unit analysis**:
- **multi_method wins on**: evolve-numeric-003 (modulo), evolve-numeric-009, evolve-numeric-011 (factorials)
- **guided_format wins on**: evolve-numeric-002 (ledger), evolve-numeric-011 (factorial)
- **Both fail on arithmetic-heavy tasks**: evolve-numeric-000 through -001, -004 through -010

**Failure pattern root cause**: **Format fragility**. In 9/12 multi_method failures, the answer was computed correctly but extraction failed:
- Output extracted: `{result}\")` or `{digit_sum}\")` (print statement wrappers, not "ANSWER: " lines)
- Model calculated right but embedded answer in print() calls instead of pure "ANSWER: <value>" format
- Example (evolve-numeric-000): Independent verification showed digit_sum was actually correct, but extracted as print statement

**Why multi_method succeeds more**: The 3-turn structure + independent verification catches some errors even with format noise; the compare turn forces a second clean statement.

**Takeaway for iter2**: 
1. **Axis A (prompt)**: Stricter format enforcement won't fully help—models resist pure "respond with only one line" instructions. 
2. **Axis F (extraction)**: Better parsing of ANSWER: lines + stripping markdown (* and **) would fix ~5 tasks immediately.
3. **Axis C (mechanism)**: Consider Python *execution* (via tools.python) to get clean stdout instead of parsing LLM text—guarantees arithmetic correctness.

---

## Hypotheses & Prototypes

**Hypothesis 1** (Axis F+A: Robust Extraction + Markdown Cleanup):
Better extraction of ANSWER: lines + markdown stripping will recover ~40% of multi_method's false negatives. The calculations are correct; only parsing fails.

*Trace prototype (evolve-numeric-000: 41! digit sum)*:
- multi_method Turn 2 output (verify): `...digit_sum = 144\n\nprint(f"CORRECT ANSWER: {digit_sum}")` 
- Current extraction: last line = `print(f"CORRECT ANSWER: {digit_sum}")` ✗
- Robust extraction: scan for "ANSWER:" case-insensitive, extract digits after it, strip **, __ → **144** ✓

**Hypothesis 2** (Axis F+C: Code Execution + Stdout Parsing):
Use tools.python(code) to execute LLM-generated code and parse "ANSWER: " from stdout. This guarantees arithmetic correctness and gives cleaner structured output than parsing LLM text.

*Trace prototype (same task)*:
- LLM generates: `import math; f=math.factorial(41); print(f"ANSWER: {sum(int(d) for d in str(f))}")`
- Harness: tools.python(code) → stdout: `"ANSWER: 144\n"`
- Extraction: regex "ANSWER: (\d+)" → 144 ✓
- Verify turn: different method (e.g., digit-by-digit parsing) confirms → agreement ✓

---

```json
{
  "iteration": 2,
  "candidates": [
    {
      "name": "clean_extraction",
      "base_system": "multi_method",
      "hypothesis": "Better ANSWER line extraction + markdown stripping recovers false negatives. The harness's extraction logic is the bottleneck, not the calculations.",
      "axis": "exploitation",
      "components": ["axis:F (robust extraction/parsing)", "axis:A (prompt clarity for ANSWER: format)"]
    },
    {
      "name": "code_execution",
      "base_system": "multi_method",
      "hypothesis": "Executing LLM-generated Python code via tools.python() and parsing stdout guarantees arithmetic correctness and cleaner extraction than parsing unstructured LLM text.",
      "axis": "exploration",
      "components": ["axis:F (code execution via tools)", "axis:C (retrieval via stdout parsing)"]
    }
  ]
}
```

---

## Candidate 1: clean_extraction

Based on multi_method with improved extraction logic in the harness.
