# Iteration 2

## Step 0: Post-Eval Report (Iteration 1)

=== FILE: reports/iter001.md ===
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

=== FILE: agents/verification_harness/harness.py ===
def solve(question, llm, tools, files):
    """
    Verification harness: Two-pass approach with independent verification.
    First pass computes the answer; second pass asks the model to independently
    verify it, which can catch arithmetic errors through recalculation.
    """
    system = files.get("prompts/system.md", "")
    prompt_template = files["prompts/task.md"]
    prompt = prompt_template.replace("{question}", question)
    
    # ===== PASS 1: Initial computation =====
    first_prompt = f"""{prompt}

Solve this problem step by step, showing all your work. When you have your final answer, output it on a line in this exact format:

ANSWER: <value>

Do not add any text before or after the answer on that line."""
    
    reply1 = llm(first_prompt, system=system)
    
    # Extract tentative answer
    tentative_answer = None
    for line in reply1.strip().splitlines():
        line_stripped = line.strip()
        if line_stripped.startswith("ANSWER:"):
            tentative_answer = line_stripped[len("ANSWER:"):].strip()
            break
    
    if not tentative_answer:
        lines = [l for l in reply1.strip().splitlines() if l.strip()]
        tentative_answer = lines[-1] if lines else ""
    
    # ===== PASS 2: Independent verification =====
    verify_prompt = f"""{prompt}

I calculated the answer to be: {tentative_answer}

Please solve this problem independently and verify the correctness of this answer. Re-calculate from scratch without relying on my answer. If my answer is correct, confirm it. If it is wrong, provide the correct answer.

Output your final verified answer on a line in this exact format:

ANSWER: <value>

Do not add any text before or after the answer on that line."""
    
    reply2 = llm(verify_prompt, system=system)
    
    # Extract verified answer
    for line in reply2.strip().splitlines():
        line_stripped = line.strip()
        if line_stripped.startswith("ANSWER:"):
            verified_answer = line_stripped[len("ANSWER:"):].strip()
            if verified_answer:
                return verified_answer
    
    # Fallback: last non-empty line from verification response
    lines = [l for l in reply2.strip().splitlines() if l.strip()]
    if lines:
        return lines[-1]
    
    # Last resort: return tentative if verification yielded nothing
    return tentative_answer

=== FILE: agents/verification_harness/prompts/system.md ===
You are a helpful assistant.

=== FILE: agents/verification_harness/prompts/task.md ===
{question}

=== FILE: agents/hybrid_code_text_harness/harness.py ===
import re

def solve(question, llm, tools, files):
    """
    Hybrid code-text harness: Request step-by-step reasoning followed by executable code.
    Separates explanation from implementation to improve code extraction and ensure
    arithmetic is delegated to Python rather than attempted in text.
    """
    system = files.get("prompts/system.md", "")
    prompt_template = files["prompts/task.md"]
    prompt = prompt_template.replace("{question}", question)
    
    # Request both reasoning and code with clear separation
    solution_prompt = f"""{prompt}

Please solve this problem using the following structure:

1. Show your step-by-step reasoning and intermediate calculations.
2. Then provide Python code in a ```python code block that outputs ONLY the final numerical answer on a single line.

The Python code must:
- Be syntactically correct and runnable
- Output exactly one line containing only the numerical answer
- Not include any explanatory text or intermediate output"""
    
    reply = llm(solution_prompt, system=system)
    
    # Extract all Python code blocks
    code_blocks = re.findall(r'```(?:python)?\s*\n(.*?)\n```', reply, re.DOTALL)
    
    # Try code blocks in reverse order (prefer the last/final code block)
    if code_blocks:
        for code in reversed(code_blocks):
            try:
                output = tools.python(code)
                # Return last non-empty line of execution output
                lines = [l.strip() for l in output.strip().splitlines() if l.strip()]
                if lines:
                    return lines[-1]
            except Exception:
                # Code block failed; try the previous one
                continue
    
    # Fallback 1: Look for ANSWER: marker in text response
    for line in reply.strip().splitlines():
        line_stripped = line.strip()
        if line_stripped.startswith("ANSWER:"):
            answer = line_stripped[len("ANSWER:"):].strip()
            if answer:
                return answer
    
    # Fallback 2: Return last non-empty line
    lines = [l for l in reply.strip().splitlines() if l.strip()]
    return lines[-1] if lines else ""

=== FILE: agents/hybrid_code_text_harness/prompts/system.md ===
You are a helpful assistant.

=== FILE: agents/hybrid_code_text_harness/prompts/task.md ===
{question}