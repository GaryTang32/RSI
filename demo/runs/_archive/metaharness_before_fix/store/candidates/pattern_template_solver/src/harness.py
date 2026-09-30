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
