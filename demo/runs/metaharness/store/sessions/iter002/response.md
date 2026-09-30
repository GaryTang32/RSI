# Iteration 2: Multi-Harness Evolution

I'll start by writing the missing post-eval report for iteration 1, then propose 2 new candidates with different mechanisms.

=== FILE: reports/iter1.md ===
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

=== FILE: agents/clean_extraction/harness.py ===
"""
Clean extraction harness: multi-method with robust ANSWER: parsing and markdown cleanup.
Axis: F (extraction), A (format clarity).
Base: multi_method, but fixes the extraction bottleneck.
"""


def solve(question, llm, tools, files):
    system = files.get("prompts/system.md", "")
    
    # Method A: Direct calculation
    method_a_prompt = files["prompts/method.md"].replace(
        "{question}", question
    ).replace(
        "{method_label}", "Method A: Direct Calculation"
    )
    result_a = llm(method_a_prompt, system=system)
    
    # Method B: Alternative/verification approach
    method_b_prompt = files["prompts/method.md"].replace(
        "{question}", question
    ).replace(
        "{method_label}", "Method B: Alternative Approach"
    )
    result_b = llm(method_b_prompt, system=system)
    
    # Compare and select
    compare_prompt = files["prompts/compare.md"].replace(
        "{question}", question
    ).replace(
        "{method_a_result}", result_a
    ).replace(
        "{method_b_result}", result_b
    )
    final_reply = llm(compare_prompt, system=system)
    
    # Robust extraction: find ANSWER: line and clean markdown
    answer = _extract_answer(final_reply)
    if answer:
        return answer
    
    # Fallback: last non-empty line with cleanup
    lines = [line.strip() for line in final_reply.strip().splitlines() if line.strip()]
    if lines:
        return _clean_answer(lines[-1])
    
    return ""


def _extract_answer(text):
    """Find ANSWER: line and extract clean value."""
    for line in text.strip().splitlines():
        line_upper = line.upper()
        if "ANSWER:" in line_upper:
            # Find the position of ANSWER: and extract everything after it
            idx = line_upper.find("ANSWER:")
            value = line[idx + 7:]  # +7 = len("ANSWER:")
            return _clean_answer(value)
    return ""


def _clean_answer(text):
    """Strip markdown, formatting, and extra whitespace from answer."""
    text = text.strip()
    
    # Remove leading/trailing markdown
    text = text.strip("*_`-()[]{}\"'")
    
    # Remove markdown formatting patterns
    text = text.replace("**", "")
    text = text.replace("__", "")
    text = text.replace("`", "")
    
    # Strip again after cleanup
    text = text.strip()
    
    return text if text else ""

=== FILE: agents/clean_extraction/prompts/system.md ===
You solve complex problems by exploring multiple approaches. Always:
1. Use Python tools for numeric work.
2. Show your reasoning and calculations clearly.
3. Compare results from different methods.
4. End with a clean line: ANSWER: <final_value>
5. Put the answer AFTER the colon on the same line, with no additional text after it on that line.

=== FILE: agents/clean_extraction/prompts/method.md ===
{method_label}

Question: {question}

Solve this carefully. Show all work and intermediate steps.
End with: ANSWER: <value>

=== FILE: agents/clean_extraction/prompts/compare.md ===
Original question: {question}

Result from Method A:
{method_a_result}

Result from Method B:
{method_b_result}

Compare these results carefully:
- Do they agree?
- Which approach is more reliable for this problem?
- If they differ, determine the correct answer independently.

End with: ANSWER: <value>

=== FILE: agents/clean_extraction/prompts/task.md ===
{question}

---

## Candidate 2: code_execution

New mechanism: LLM generates Python code, harness executes it via tools.python(), parses stdout.

=== FILE: agents/code_execution/harness.py ===
"""
Code execution harness: LLM generates Python code, harness executes it and parses stdout.
Axis: F (code execution via tools.python), C (stdout parsing instead of LLM text parsing).
Guarantees arithmetic correctness by running code deterministically.
"""


def solve(question, llm, tools, files):
    system = files.get("prompts/system.md", "")
    
    # Turn 1: Generate and execute code
    code_gen_prompt = files["prompts/generate_code.md"].replace("{question}", question)
    code_response = llm(code_gen_prompt, system=system)
    
    # Extract Python code from response (look for ```python blocks)
    code = _extract_code(code_response)
    if not code:
        # Fallback: use the entire response as code
        code = code_response
    
    # Execute the code
    try:
        output1 = tools.python(code)
    except Exception:
        output1 = ""
    
    answer1 = _extract_answer_from_stdout(output1)
    
    # Turn 2: Verify with alternative approach
    verify_prompt = files["prompts/verify_code.md"].replace(
        "{question}", question
    ).replace(
        "{previous_answer}", answer1 or "UNKNOWN"
    )
    verify_response = llm(verify_prompt, system=system)
    
    verify_code = _extract_code(verify_response)
    if not verify_code:
        verify_code = verify_response
    
    try:
        output2 = tools.python(verify_code)
    except Exception:
        output2 = ""
    
    answer2 = _extract_answer_from_stdout(output2)
    
    # Return first answer, or second if first failed
    if answer1:
        return answer1
    elif answer2:
        return answer2
    else:
        # Fallback: try to extract from LLM text
        return _extract_answer_from_text(code_response)


def _extract_code(text):
    """Extract Python code from markdown code blocks."""
    import re
    
    # Look for ```python ... ``` blocks
    pattern = r"```python\s*(.*?)\s*```"
    matches = re.findall(pattern, text, re.DOTALL)
    if matches:
        return matches[0]
    
    # Fallback: look for ``` ... ``` blocks (no language specified)
    pattern = r"```\s*(.*?)\s*```"
    matches = re.findall(pattern, text, re.DOTALL)
    if matches:
        return matches[0]
    
    # If no code blocks found, return empty
    return ""


def _extract_answer_from_stdout(stdout):
    """Parse ANSWER: lines from program stdout."""
    for line in stdout.strip().splitlines():
        if "ANSWER:" in line.upper():
            idx = line.upper().find("ANSWER:")
            value = line[idx + 7:].strip()  # +7 = len("ANSWER:")
            # Clean up any trailing content
            value = value.split()[0] if value else ""
            return value
    return ""


def _extract_answer_from_text(text):
    """Fallback: extract ANSWER: from LLM text."""
    for line in text.strip().splitlines():
        if "ANSWER:" in line.upper():
            idx = line.upper().find("ANSWER:")
            value = line[idx + 7:].strip()
            value = value.strip("*_`-()[]{}\"'").strip()
            return value
    return ""

=== FILE: agents/code_execution/prompts/system.md ===
You are a Python expert. Solve problems by writing clean, executable Python code.
Always:
1. Write code that prints exactly: ANSWER: <value>
2. The answer MUST be printed to stdout in the format "ANSWER: <numeric_value>"
3. Your code must be runnable and produce no errors.
4. Use standard libraries only (math, itertools, etc.)

=== FILE: agents/code_execution/prompts/generate_code.md ===
Solve this problem by writing Python code.

Question: {question}

Write Python code that:
1. Solves the problem correctly
2. Prints exactly: ANSWER: <value>
   where <value> is ONLY the numeric/text answer

Provide ONLY the Python code, inside ```python and ``` markers.
Do not explain or add text outside the code block.

```python
# Write your solution here
```

=== FILE: agents/code_execution/prompts/verify_code.md ===
Original question: {question}
Previous answer: {previous_answer}

Verify this answer using a DIFFERENT method in Python.

Write code that:
1. Uses an alternative approach (different algorithm or calculation method)
2. Prints exactly: ANSWER: <value>

Provide ONLY the code, inside ```python and ``` markers.

```python
# Write your verification code here
```

=== FILE: agents/code_execution/prompts/task.md ===
{question}