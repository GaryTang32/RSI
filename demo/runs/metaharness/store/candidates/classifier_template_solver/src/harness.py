"""
Classifier-template solver: Use LLM to classify problem type, then apply deterministic templates.
Axis: F (model usage: model-based type classification), A (prompt: classification-first architecture).
More general than hardcoded regex; cheaper than full code gen when classification succeeds.
Falls back to full code generation if classification fails.
"""

import re


def solve(question, llm, tools, files):
    system = files.get("prompts/system.md", "")
    
    # Turn 1: Classify problem type (cheap LLM call)
    classify_prompt = files["prompts/classify.md"].replace("{question}", question)
    classify_response = llm(classify_prompt, system=system)
    problem_type = classify_response.strip().lower().split()[0]  # Take first word only
    
    # Turn 2: Apply template based on classification
    answer = _apply_template(problem_type, question, tools)
    if answer:
        return answer
    
    # Fallback: Full LLM code generation (if classification didn't work)
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


def _apply_template(problem_type, question, tools):
    """Execute a template based on classified problem type."""
    
    if "digit_sum_factorial" in problem_type or "factorial_digit" in problem_type:
        match = re.search(r'(\d+)!', question)
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
    
    elif "power_digit" in problem_type or "digit_sum_power" in problem_type:
        match = re.search(r'(\d+)\^(\d+)', question)
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
    
    elif "modulo" in problem_type or "mod" in problem_type:
        match = re.search(r'(\d+)\s+mod\s+(\d+)', question)
        if match:
            n, mod = int(match.group(1)), int(match.group(2))
            code = f'print(f"ANSWER: {{{n} % {mod}}}")'
            try:
                output = tools.python(code)
                ans = _extract_answer_from_stdout(output)
                if ans:
                    return ans
            except:
                pass
    
    elif "bit_count" in problem_type or "1_bits" in problem_type or "binary" in problem_type:
        match = re.search(r'(\d+)\^(\d+)', question)
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
    
    elif "ledger" in problem_type or "arithmetic" in problem_type or "balance" in problem_type:
        ans = _try_pattern_ledger(question, tools)
        if ans:
            return ans
    
    # Unknown type or template failed; return None to trigger fallback
    return None


def _try_pattern_ledger(question, tools):
    """
    Parse ledger-style multi-operation arithmetic.
    Recognizes: "starts at X", then "Add Y", "subtract Z", "N payments of M", etc.
    """
    q = question.lower()
    
    # Extract starting balance
    match = re.search(r'(?:starts?\s+at|begins?\s+at|balance[:\s]*)\s*(\d+)', q)
    if not match:
        return None
    
    starting = int(match.group(1))
    code_lines = [f"balance = {starting}"]
    
    # Extract all operations
    operations = re.findall(
        r'\b(add|subtract|plus|minus)\s+(.*?)(?=\b(?:add|subtract|plus|minus|and)|\.$)',
        question,
        re.IGNORECASE | re.DOTALL
    )
    
    for op, operand in operations:
        op_lower = op.lower()
        sign = '+' if op_lower in ['add', 'plus'] else '-'
        
        numbers = re.findall(r'\d+', operand)
        
        if len(numbers) == 0:
            continue
        elif len(numbers) == 1:
            value = numbers[0]
        else:
            value = f"({numbers[0]} * {numbers[1]})"
        
        code_lines.append(f"balance {sign}= {value}")
    
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
