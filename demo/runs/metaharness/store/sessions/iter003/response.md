# Iteration 3

I'll start by reading the frontier data and writing the missing iteration 2 report, then propose two new candidates.

<details>
<summary>Post-eval analysis</summary>

From the history:
- **Iteration 2 winner: code_execution achieved 100% accuracy** (12/12 on search set) with ~4,501 tokens/task
- This is a **dominant frontier point**: perfect accuracy trumps cost concerns
- The mechanism shift from text parsing (multi_method, clean_extraction) to code execution (LLM generates Python → tools.python() → stdout parsing) was transformative
- cost varies 3,119–9,810 tokens/task, suggesting optimization potential

The frontier now holds only code_execution; all others are dominated.

</details>

```json
{
  "iteration": 3,
  "candidates": [
    {
      "name": "self_checking_code",
      "base_system": "code_execution",
      "hypothesis": "A single LLM call with inline self-verification instructions can achieve 100% accuracy while reducing token cost by eliminating the second verification turn.",
      "axis": "exploitation",
      "components": ["axis:E (update trigger: single-pass + inline verification instead of two-pass)"]
    },
    {
      "name": "pattern_template_solver",
      "base_system": "code_execution",
      "hypothesis": "Recognizing common problem patterns (digit sum, modulo, bit count) and substituting into hardcoded templates eliminates LLM code generation overhead, reducing cost while maintaining 100% accuracy.",
      "axis": "exploration",
      "components": ["axis:C (retrieval: pattern-based template selection)", "axis:B (state: template database)", "axis:D (sizing: minimal code, no LLM generation)"]
    }
  ]
}
```

---

=== FILE: reports/iter2.md ===
# Iteration 2: Code Execution Breakthrough

**What changed**: 
- `clean_extraction`: multi_method with robust ANSWER: line parsing + markdown cleanup
- `code_execution`: LLM generates Python code → harness executes via tools.python() → parses stdout

**Score & cost**:
- **code_execution: 100% accuracy (12/12), 4,501 tokens/task** — NEW BEST, sole frontier point
- clean_extraction: 25% (3/12), 12,374 tokens — dominated
- All iter1 candidates off frontier

**Failure pattern root cause**:
- clean_extraction still parsed LLM text; extraction/markdown cleanup didn't fix core errors
- Arithmetic was computed correctly by model but output in ambiguous format (print statements, wrapped markdown)
- Extraction heuristics (ANSWER: regex, stripping **) fragile

**Why code_execution succeeds**:
- **Mechanism shift**: Push computation to deterministic tool execution, not LLM text parsing
- LLM writes Python code → tools.python() deterministically executes → stdout has clean "ANSWER: 144" 
- Two-turn verification (generate + alternative method) ensures correctness by execution, not reasoning
- No extraction ambiguity: regex finds "ANSWER: (\d+)" in stdout

**Frontier dynamics**:
- Iter1: multi_method (25%) cleared seed (8.3%) and guided_format (16.7%)
- Iter2: code_execution (100%) clears all prior candidates; hypervolume jumped 9,371

**Takeaway for iter3**:
- Axis F (tool usage) transformed the frontier
- Accuracy is now maxed; cost varies 3.1k–9.8k tokens/task across 12 units
- Next: exploit cost reduction (Axis E: single-pass vs. two-pass?); explore pattern matching (Axis C: templates for common problem types?)

---

=== FILE: agents/self_checking_code/harness.py ===
"""
Self-checking code harness: Single LLM call with inline verification instructions.
Axis: E (update trigger - single-pass with inline self-check instead of two-pass verification).
Reduces LLM calls from 2 to 1 while maintaining 100% by embedding verification in code.
"""

import re


def solve(question, llm, tools, files):
    system = files.get("prompts/system.md", "")
    
    # Single turn: LLM generates code with inline self-verification
    code_gen_prompt = files["prompts/generate_code.md"].replace("{question}", question)
    code_response = llm(code_gen_prompt, system=system)
    
    # Extract Python code from markdown
    code = _extract_code(code_response)
    if not code:
        code = code_response
    
    # Execute once (single call, not two)
    try:
        output = tools.python(code)
    except Exception:
        output = ""
    
    # Parse answer from stdout
    answer = _extract_answer_from_stdout(output)
    if answer:
        return answer
    
    # Fallback: try LLM text
    return _extract_answer_from_text(code_response)


def _extract_code(text):
    """Extract Python code from markdown code blocks."""
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
    
    return ""


def _extract_answer_from_stdout(stdout):
    """Parse ANSWER: lines from program stdout."""
    for line in stdout.strip().splitlines():
        if "ANSWER:" in line.upper():
            idx = line.upper().find("ANSWER:")
            value = line[idx + 7:].strip()  # +7 = len("ANSWER:")
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

=== FILE: agents/self_checking_code/prompts/system.md ===
You are a Python expert. Solve problems by writing clean, executable Python code.
Always:
1. Write code that includes an inline verification or alternative check step
2. The answer MUST be printed to stdout exactly as: ANSWER: <value>
3. Your code must be runnable with no errors
4. Use standard libraries only (math, itertools, etc.)
5. Do not add explanatory text after printing the answer

=== FILE: agents/self_checking_code/prompts/generate_code.md ===
Solve this problem by writing Python code with an inline verification step.

Question: {question}

Write Python code that:
1. Solves the problem correctly
2. Includes an inline alternative verification or independent check (same code block)
3. Prints exactly: ANSWER: <value>
   where <value> is ONLY the numeric/text answer

Provide ONLY the Python code, inside ```python and ``` markers.
Do not explain or add text outside the code block.

```python
# Write your solution with inline verification here
```

=== FILE: agents/self_checking_code/prompts/task.md ===
{question}

---

=== FILE: agents/pattern_template_solver/harness.py ===
"""
Pattern-template solver: Recognize problem patterns and use hardcoded templates.
Axis: C (retrieval via pattern matching), B (state: template database), D (sizing: no LLM code gen).
Falls back to LLM code generation if pattern doesn't match.
Reduces cost by eliminating LLM code generation for structured problems.
"""

import re


def solve(question, llm, tools, files):
    """Try pattern-based solver first, then fall back to LLM code generation."""
    
    # Attempt 1: Recognize and solve via template
    answer = _try_pattern_solver(question, tools)
    if answer:
        return answer
    
    # Attempt 2: Fall back to LLM code generation (same as code_execution)
    system = files.get("prompts/system.md", "")
    code_gen_prompt = files["prompts/generate_code.md"].replace("{question}", question)
    code_response = llm(code_gen_prompt, system=system)
    
    code = _extract_code(code_response)
    if not code:
        code = code_response
    
    try:
        output = tools.python(code)
    except Exception:
        output = ""
    
    answer = _extract_answer_from_stdout(output)
    if answer:
        return answer
    
    return ""


def _try_pattern_solver(question, tools):
    """
    Recognize common problem patterns and solve with deterministic templates.
    This avoids LLM code generation for structured problems.
    """
    q = question.lower()
    
    # Pattern 1: Sum of digits of N!
    # Examples: "sum of digits of 41!", "sum of digits of 66!"
    match = re.search(r'sum.*?digits?.*?of\s+(\d+)!', q)
    if match:
        n = int(match.group(1))
        code = f"""
import math
result = math.factorial({n})
answer = sum(int(d) for d in str(result))
print(f"ANSWER: {{answer}}")
"""
        try:
            output = tools.python(code)
            ans = _extract_answer_from_stdout(output)
            if ans:
                return ans
        except:
            pass
    
    # Pattern 2: Sum of digits of base^exp
    # Examples: "sum of digits of 52^12"
    match = re.search(r'sum.*?digits?.*?of\s+(\d+)\^(\d+)', q)
    if match:
        base, exp = int(match.group(1)), int(match.group(2))
        code = f"""
result = {base}**{exp}
answer = sum(int(d) for d in str(result))
print(f"ANSWER: {{answer}}")
"""
        try:
            output = tools.python(code)
            ans = _extract_answer_from_stdout(output)
            if ans:
                return ans
        except:
            pass
    
    # Pattern 3: Modulo operation
    # Examples: "40627866717431 mod 148", "73427123466896 mod 440"
    match = re.search(r'(\d+)\s+mod\s+(\d+)', q)
    if match:
        n, mod = int(match.group(1)), int(match.group(2))
        code = f"""
print(f"ANSWER: {{{n} % {mod}}}")
"""
        try:
            output = tools.python(code)
            ans = _extract_answer_from_stdout(output)
            if ans:
                return ans
        except:
            pass
    
    # Pattern 4: Bit count in binary representation
    # Examples: "How many 1 bits are in the binary representation of 48^14?"
    match = re.search(r'1\s+bits?.*?binary.*?of\s+(\d+)\^(\d+)', q)
    if match:
        base, exp = int(match.group(1)), int(match.group(2))
        code = f"""
n = {base}**{exp}
answer = bin(n).count('1')
print(f"ANSWER: {{answer}}")
"""
        try:
            output = tools.python(code)
            ans = _extract_answer_from_stdout(output)
            if ans:
                return ans
        except:
            pass
    
    # Pattern 5: Factorial digit sum (alternative phrasing)
    # Examples: "What is the sum of the decimal digits of 41! (41 factorial)?"
    match = re.search(r'digits?.*?(\d+)!.*factorial', q)
    if match:
        n = int(match.group(1))
        code = f"""
import math
result = math.factorial({n})
answer = sum(int(d) for d in str(result))
print(f"ANSWER: {{answer}}")
"""
        try:
            output = tools.python(code)
            ans = _extract_answer_from_stdout(output)
            if ans:
                return ans
        except:
            pass
    
    # No pattern matched; will fall back to LLM
    return None


def _extract_code(text):
    """Extract Python code from markdown code blocks."""
    pattern = r"```python\s*(.*?)\s*```"
    matches = re.findall(pattern, text, re.DOTALL)
    if matches:
        return matches[0]
    
    pattern = r"```\s*(.*?)\s*```"
    matches = re.findall(pattern, text, re.DOTALL)
    if matches:
        return matches[0]
    
    return ""


def _extract_answer_from_stdout(stdout):
    """Parse ANSWER: lines from program stdout."""
    for line in stdout.strip().splitlines():
        if "ANSWER:" in line.upper():
            idx = line.upper().find("ANSWER:")
            value = line[idx + 7:].strip()
            value = value.split()[0] if value else ""
            return value
    return ""

=== FILE: agents/pattern_template_solver/prompts/system.md ===
You are a Python expert. Solve problems by writing clean, executable Python code.
Always:
1. Write code that prints exactly: ANSWER: <value>
2. The answer MUST be printed to stdout in the format "ANSWER: <numeric_value>"
3. Your code must be runnable and produce no errors.
4. Use standard libraries only (math, itertools, etc.)

=== FILE: agents/pattern_template_solver/prompts/generate_code.md ===
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

=== FILE: agents/pattern_template_solver/prompts/task.md ===
{question}