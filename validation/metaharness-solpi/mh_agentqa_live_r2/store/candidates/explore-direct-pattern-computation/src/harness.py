"""
Direct pattern computation: parse question for computation patterns, generate
precise Python code, execute via tools. Tools-first execution with LLM fallback.
Avoids LLM planning ambiguity that fails on complex multi-step calculations.
"""

import re


def solve(question, llm, tools, files):
    """
    Strategy: Try direct pattern-based computation first.
    Only use LLM if tool execution fails.
    """
    
    # Step 1: Detect computation pattern and try direct execution
    answer = execute_direct_computation(question, tools)
    if answer:
        return answer
    
    # Step 2: Fallback to LLM + robust extraction
    system = files.get("prompts/system.md", "")
    prompt = files["prompts/task.md"].replace("{question}", question)
    reply = llm(prompt, system=system)
    answer = extract_answer_robust(reply)
    
    return answer


def execute_direct_computation(question, tools):
    """
    Detect computation type from question text and execute via tools.
    Handles: modular arithmetic, digit sum, bit counting, ledger calculations.
    """
    
    # Pattern 1: Modular exponentiation (X^Y mod Z)
    result = handle_modular_arithmetic(question, tools)
    if result:
        return result
    
    # Pattern 2: Digit sum of power
    result = handle_digit_sum(question, tools)
    if result:
        return result
    
    # Pattern 3: Bit counting
    result = handle_bit_counting(question, tools)
    if result:
        return result
    
    # Pattern 4: Ledger calculation
    result = handle_ledger_calculation(question, tools)
    if result:
        return result
    
    return None


def handle_modular_arithmetic(question, tools):
    """Execute X^Y mod Z pattern."""
    pattern = r'(?:remainder|modulo|mod).*?(\d+)\s*\^\s*(\d+).*?(?:divided by|mod|modulo)\s*(\d+)'
    match = re.search(pattern, question, re.IGNORECASE)
    
    if match:
        base, exp, modulus = match.groups()
        try:
            code = f"print(pow({base}, {exp}, {modulus}))"
            result = tools.python(code)
            numbers = re.findall(r'\d+', result)
            if numbers:
                return numbers[-1]
        except:
            pass
    
    return None


def handle_digit_sum(question, tools):
    """Execute digit sum of X^Y pattern."""
    pattern = r'sum.*?(?:decimal\s+)?digits.*?(\d+)\s*\^\s*(\d+)'
    match = re.search(pattern, question, re.IGNORECASE)
    
    if match:
        base, exp = match.groups()
        try:
            code = f"n = {base}**{exp}\nprint(sum(int(d) for d in str(n)))"
            result = tools.python(code)
            numbers = re.findall(r'\d+', result)
            if numbers:
                return numbers[-1]
        except:
            pass
    
    return None


def handle_bit_counting(question, tools):
    """Execute bit counting in X^Y pattern."""
    pattern = r'(?:how many|count).*?1\s*bits?.*?(\d+)\s*\^\s*(\d+)'
    match = re.search(pattern, question, re.IGNORECASE)
    
    if match:
        base, exp = match.groups()
        try:
            code = f"n = {base}**{exp}\nprint(bin(n).count('1'))"
            result = tools.python(code)
            numbers = re.findall(r'\d+', result)
            if numbers:
                return numbers[-1]
        except:
            pass
    
    return None


def handle_ledger_calculation(question, tools):
    """Execute ledger calculation pattern."""
    
    # Check for ledger keywords
    if not re.search(r'(?:ledger|account|balance|starts at)', question, re.IGNORECASE):
        return None
    
    # Extract starting balance
    start_match = re.search(r'(?:starts?\s+at|starting\s+(?:at|balance))\s+(\d+)', question, re.IGNORECASE)
    if not start_match:
        return None
    
    try:
        start_val = int(start_match.group(1))
        code = generate_ledger_code(question, start_val)
        if code:
            result = tools.python(code)
            numbers = re.findall(r'\d+', result.strip())
            if numbers:
                return numbers[-1]
    except:
        pass
    
    return None


def generate_ledger_code(question, starting_balance):
    """
    Generate Python code for ledger calculation.
    Parses: "Add X, subtract Y, add N payments of Z, ..." patterns.
    """
    code_lines = [f"balance = {starting_balance}"]
    
    # Find all operations with their positions
    all_ops = []
    
    # Simple operations: "Add X" or "Subtract X"
    for match in re.finditer(r'(add|subtract)\s+(\d+)', question, re.IGNORECASE):
        op, amount = match.groups()
        all_ops.append((match.start(), op.lower(), int(amount), 'direct'))
    
    # Batch operations: "N payments/deposits of M" or "N refunds of M"
    for match in re.finditer(
        r'(?:add\s+)?(\d+)\s+(payments?|deposits?)\s+of\s+(\d+)',
        question, re.IGNORECASE
    ):
        count, kind, amount = match.groups()
        total = int(count) * int(amount)
        all_ops.append((match.start(), 'add', total, 'batch'))
    
    for match in re.finditer(
        r'subtract\s+(\d+)\s+(refunds?)\s+of\s+(\d+)',
        question, re.IGNORECASE
    ):
        count, kind, amount = match.groups()
        total = int(count) * int(amount)
        all_ops.append((match.start(), 'subtract', total, 'batch'))
    
    # Sort by position in text
    all_ops.sort(key=lambda x: x[0])
    
    # Generate code for each operation
    for _, op, amount, _ in all_ops:
        if op == 'add':
            code_lines.append(f"balance += {amount}")
        elif op == 'subtract':
            code_lines.append(f"balance -= {amount}")
    
    code_lines.append("print(balance)")
    return "\n".join(code_lines)


def extract_answer_robust(text):
    """
    Robust extraction fallback: extract numeric answer from LLM response.
    Uses multiple strategies: ANSWER: pattern, bold numbers, numbers after keys, last number.
    """
    
    # Strategy 1: Explicit ANSWER: pattern
    match = re.search(r'ANSWER:\s*([^\n]+)', text, re.IGNORECASE)
    if match:
        value = match.group(1).strip()
        value = re.sub(r'\*\*|__', '', value).strip()
        if value and re.search(r'\d', value):
            return value
    
    # Strategy 2: Bold numbers
    bold_numbers = re.findall(r'\*\*(\d+(?:[,\d]*)?)\*\*', text)
    if bold_numbers:
        return bold_numbers[-1].replace(',', '')
    
    # Strategy 3: Numbers after key phrases
    colon_matches = re.findall(
        r'(?:answer|result|value|remainder|sum|count|balance|total|is|equals?|final)[\s:=]+(\d+(?:[,\d]*)?)',
        text, re.IGNORECASE
    )
    if colon_matches:
        return colon_matches[-1].replace(',', '')
    
    # Strategy 4: Last standalone number
    all_numbers = re.findall(r'\b(\d+(?:[,\d]*)?)\b', text)
    if all_numbers:
        return all_numbers[-1].replace(',', '')
    
    # Strategy 5: Last non-empty line
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    return lines[-1] if lines else ""
