# Iteration 1 Analysis & Output

## Step 0: Post-eval Report for Iteration 0

=== FILE: reports/iter0.md ===
# Iteration 0: Baseline Seed Harness Report

**What changed**: Baseline only—single LLM call, last-line extraction.

**Score & cost**: 8.3% accuracy (1/12 correct) on search set; ~1,526 tokens per task.

**Unit performance**:
- **Success**: evolve-numeric-011 (sum of digits of 13^10 = 67)
- **Failures** (11/12): All were arithmetic or formatting errors:
  - Off-by-N errors in digit sums (evolve-numeric-000: 198 vs 144; -004: 81 vs 100)
  - Wrong modulo results (evolve-numeric-001: 31 vs 91; evolve-numeric-003: 16 vs 56)
  - Format mismatches (evolve-numeric-002: "-183,764" vs "-183764"; evolve-numeric-008: "253" appeared mid-explanation)
  - Non-numeric last lines (evolve-numeric-010: explanation about binary patterns instead of "14")

**Why**: Baseline has no verification, no tool enforcement, and no output format control. The model calculates but often makes arithmetic mistakes and doesn't isolate answers clearly.

**Takeaway**: Next candidates should (1) explicitly enforce Python tool usage, (2) add verification/multi-attempt steps, and (3) require "ANSWER: <value>" format to isolate numeric answers.

---

## Hypotheses & Prototypes

**Hypothesis 1** (Axis A+F: Prompt + Model-Written Verification):  
Strict prompting + multi-turn verification reduces errors. If the prompt enforces tool use and adds a verification pass, the model will catch arithmetic mistakes.  
*Prototype walk-through (evolve-numeric-001: 40627866717431 mod 148)*:
- Turn 1 (calculate): LLM uses Python, computes 40627866717431 % 148, gets result (correct or wrong)
- Turn 2 (verify): LLM re-computes independently to check, formats as "ANSWER: <value>"
- If Turn 1 was wrong, Turn 2 might catch it via re-verification

**Hypothesis 2** (Axis C+B: Multiple methods + State):  
Multiple independent solution attempts increase reliability. If the harness collects 2+ methods and has the LLM compare, the correct answer is more likely to be selected.  
*Prototype walk-through (same task)*:
- Attempt A: "Compute directly using Python"
- Attempt B: "Verify using division reconstruction: a = (a // mod) * mod + (a % mod)"
- Compare turn: LLM checks if both agree, picks the right one, formats "ANSWER: <value>"
- Mismatch alerts the model to recompute

---

```json
{
  "iteration": 1,
  "candidates": [
    {
      "name": "guided_format",
      "base_system": "seed",
      "hypothesis": "Strict multi-turn prompts with explicit tool enforcement and verification reduce arithmetic errors by forcing recalculation and explicit ANSWER: <value> format.",
      "axis": "exploitation",
      "components": ["axis:A (prompt template)", "axis:F (model-written verification)"]
    },
    {
      "name": "multi_method",
      "base_system": "seed",
      "hypothesis": "Multiple independent solution attempts with comparison increase robustness; disagreement triggers re-verification, catching errors the model might make on a single pass.",
      "axis": "exploration",
      "components": ["axis:C (selection algorithm - multi-attempt comparison)", "axis:B (state storage - intermediate results)", "axis:F (model usage - cross-check reasoning)"]
    }
  ]
}
```

---

## Candidate 1: guided_format

=== FILE: agents/guided_format/harness.py ===
"""
Multi-turn guided harness: calculate with tool enforcement, then verify and format.
Axis: A (prompt), F (model-written verification).
"""


def solve(question, llm, tools, files):
    system = files.get("prompts/system.md", "")
    
    # Turn 1: Calculate with explicit tool usage
    calc_prompt = files["prompts/calculate.md"].replace("{question}", question)
    calc_reply = llm(calc_prompt, system=system)
    
    # Turn 2: Verify and format answer
    verify_prompt = files["prompts/verify.md"].replace(
        "{question}", question
    ).replace(
        "{calculation}", calc_reply
    )
    verify_reply = llm(verify_prompt, system=system)
    
    # Extract final answer: prioritize ANSWER: line
    for line in verify_reply.strip().splitlines():
        line_upper = line.upper()
        if "ANSWER:" in line_upper:
            return line.strip()
    
    # Fallback: last non-empty line
    lines = [line for line in verify_reply.strip().splitlines() if line.strip()]
    return lines[-1] if lines else ""

=== FILE: agents/guided_format/prompts/system.md ===
You are a precise computational assistant. You solve problems by:
1. Always using Python tools for all numeric calculations.
2. Showing every step clearly.
3. Verifying your work before providing a final answer.
4. Ending every response with exactly: ANSWER: <final_value>

=== FILE: agents/guided_format/prompts/calculate.md ===
Solve this problem step by step. Use Python code for all numeric operations.

Question: {question}

Show your Python code and its output. Be precise with all calculations.

=== FILE: agents/guided_format/prompts/verify.md ===
Original question: {question}

Earlier calculation:
{calculation}

Now verify this answer is correct:
- Recompute the result independently using Python.
- Check your arithmetic.
- If you find an error, recalculate.

End your response with: ANSWER: <value>

---

## Candidate 2: multi_method

=== FILE: agents/multi_method/harness.py ===
"""
Multi-method harness: solve via two independent approaches, then compare and decide.
Axis: C (selection via comparison), B (state: intermediate results), F (model usage: cross-verification).
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
    
    # Extract final answer: prioritize ANSWER: line
    for line in final_reply.strip().splitlines():
        line_upper = line.upper()
        if "ANSWER:" in line_upper:
            return line.strip()
    
    # Fallback: last non-empty line
    lines = [line for line in final_reply.strip().splitlines() if line.strip()]
    return lines[-1] if lines else ""

=== FILE: agents/multi_method/prompts/system.md ===
You solve complex problems by exploring multiple approaches. Always:
1. Use Python tools for numeric work.
2. Show your reasoning and calculations clearly.
3. Compare results from different methods.
4. End with: ANSWER: <final_value>

=== FILE: agents/multi_method/prompts/method.md ===
{method_label}

Question: {question}

Solve this carefully using Python. Show all steps and your final result clearly.

=== FILE: agents/multi_method/prompts/compare.md ===
Original question: {question}

Result from Method A:
{method_a_result}

Result from Method B:
{method_b_result}

Compare these two results:
- Do they agree?
- Which approach is more reliable?
- If they differ, determine the correct answer through independent verification.

End with: ANSWER: <value>