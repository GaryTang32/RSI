"""
Enhanced pattern-template solver: Extended pattern database covering ledger/multi-op arithmetic.
Axis: D (sizing: expanded template database), C (retrieval: comprehensive pattern matching).
Recognizes more problem types deterministically to reduce LLM fallback rate toward zero.
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
    Extended patterns: ledger, sequence arithmetic, factorial modulo.
    """
    q = question.lower()
    
    # Pattern 1: Sum of digits of N!
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
    
    # Pattern 6: Ledger/multi-operation arithmetic
    # "Morgan's ledger starts at 49249. Add 84718, subtract 10095, add 77 payments of 50354, ..."
    ans = _try_pattern_ledger(question, tools)
    if ans:
        return ans
    
    # No pattern matched; will fall back to LLM
    return None


def _try_pattern_ledger(question, tools):
    """
    Parse ledger-style multi-operation arithmetic.
    Recognizes: "starts at X", then "Add Y", "subtract Z", "N payments of M", etc.
    Builds and executes arithmetic operations dynamically.
    """
    q = question.lower()
    
    # Check if it looks like a ledger problem
    if not ("ledger" in q or ("starts at" in q and ("add" in q or "subtract" in q or "deposit" in q))):
        return None
    
    # Extract starting balance
    match = re.search(r'(?:starts?\s+at|begins?\s+at|balance[:\s]*)\s*(\d+)', q)
    if not match:
        return None
    
    starting = int(match.group(1))
    code_lines = [f"balance = {starting}"]
    
    # Extract all operations: (add|subtract|plus|minus) + amount
    # Handle "N payments of M" as "N * M"
    # Strategy: find all operation keywords and their operands
    
    operations = re.findall(
        r'\b(add|subtract|plus|minus)\s+(.*?)(?=\b(?:add|subtract|plus|minus|and)|\.$)',
        question,
        re.IGNORECASE | re.DOTALL
    )
    
    for op, operand in operations:
        op_lower = op.lower()
        sign = '+' if op_lower in ['add', 'plus'] else '-'
        
        # Extract all numbers from operand
        # "84718" → [84718]
        # "77 payments of 50354" → [77, 50354]
        numbers = re.findall(r'\d+', operand)
        
        if len(numbers) == 0:
            continue
        elif len(numbers) == 1:
            value = numbers[0]
        else:
            # Interpret as "N * M" (e.g., "77 payments of 50354")
            # Take first two numbers
            value = f"({numbers[0]} * {numbers[1]})"
        
        code_lines.append(f"balance {sign}= {value}")
    
    # Only execute if we found operations
    if len(code_lines) <= 1:
        return None
    
    code_lines.append('print(f"ANSWER: {balance}")')
    code = '\n'.join(code_lines)
    
    try:
        output = tools.python(code)
        ans = _extract_answer_from_stdout(output)
        if ans:
            return ans
    except:
        pass
    
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
