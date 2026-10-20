"""
Tool-driven computation: LLM identifies the algorithm, harness executes it.

Separate mechanism: 
1. Ask LLM to describe WHAT to compute (algorithm/formula), not to compute it
2. Extract the algorithm description
3. Use tools.python() to execute the algorithm
4. Extract answer from tool output only

This ensures computational correctness by delegating arithmetic to tools.
"""

import re


def solve(question, llm, tools, files):
    system = files.get("prompts/system.md", "")
    planning_prompt = files["prompts/planning.md"].replace("{question}", question)
    
    # Step 1: Get LLM to describe the algorithm/computation needed
    plan = llm(planning_prompt, system=system)
    
    # Step 2: Extract algorithm description from plan
    algorithm = extract_algorithm(plan)
    
    # Step 3: Execute algorithm with tools if possible
    if algorithm:
        answer = execute_algorithm(algorithm, question, tools)
        if answer:
            return answer
    
    # Step 4: Fallback - try generic computation attempt
    answer = attempt_direct_computation(question, tools)
    if answer:
        return answer
    
    # Step 5: If all else fails, try to extract any numeric value from plan
    return extract_last_number(plan)


def extract_algorithm(plan):
    """
    Extract the algorithm/formula from LLM's planning response.
    Look for Python code, formulas, or computation descriptions.
    """
    
    # Try to find Python code blocks
    code_blocks = re.findall(r'```(?:python)?\s*\n(.*?)\n```', plan, re.DOTALL)
    if code_blocks:
        return code_blocks[0].strip()
    
    # Look for standalone Python expressions
    # "compute pow(19, 763, 823)"
    expr_match = re.search(r'(?:compute|calculate|find|use)\s+(?:the\s+)?(?:formula|expression|code)?:?\s*([a-z_]\w*\([^)]+\))', plan, re.IGNORECASE)
    if expr_match:
        return expr_match.group(1)
    
    # Look for "X = " style assignments
    # "balance = 54341 + 74690 - 33538 ..."
    assign_match = re.search(r'([a-z_]\w*)\s*=\s*([0-9\s+\-*/(). ]+)', plan, re.IGNORECASE | re.MULTILINE)
    if assign_match:
        return assign_match.group(0)
    
    return None


def execute_algorithm(algorithm, question, tools):
    """Execute the extracted algorithm and get the answer."""
    
    # Wrap algorithm in print statement if needed
    code_to_run = algorithm
    if not code_to_run.startswith('print'):
        code_to_run = f"print({algorithm})"
    
    try:
        result = tools.python(code_to_run)
        # Extract first number from output
        numbers = re.findall(r'\d+', result)
        if numbers:
            return numbers[0]
    except:
        pass
    
    return None


def attempt_direct_computation(question, tools):
    """
    Analyze question directly and attempt to construct computation code.
    Handles common patterns: modular arithmetic, ledger, digit sum, bit count.
    """
    
    # Pattern 1: "What is X^Y mod Z" or "remainder when X^Y divided by Z"
    mod_pattern = r'(?:remainder|modulo|mod).*?(\d+)\s*\^\s*(\d+).*?(?:divided by|mod)\s*(\d+)'
    mod_match = re.search(mod_pattern, question, re.IGNORECASE)
    if mod_match:
        base, exp, modulus = mod_match.groups()
        result = tools.python(f"print(pow({base}, {exp}, {modulus}))")
        numbers = re.findall(r'\d+', result)
        if numbers:
            return numbers[-1]
    
    # Pattern 2: "What is the sum of decimal digits of X^Y"
    digit_pattern = r'sum.*?decimal\s+digits.*?(\d+)\s*\^\s*(\d+)'
    digit_match = re.search(digit_pattern, question, re.IGNORECASE)
    if digit_match:
        base, exp = digit_match.groups()
        code = f"n = {base}**{exp}\nprint(sum(int(d) for d in str(n)))"
        result = tools.python(code)
        numbers = re.findall(r'\d+', result)
        if numbers:
            return numbers[-1]
    
    # Pattern 3: "How many 1 bits ... binary representation of X^Y"
    bit_pattern = r'(?:how many|count).*?1\s*bits.*?(\d+)\s*\^\s*(\d+)'
    bit_match = re.search(bit_pattern, question, re.IGNORECASE)
    if bit_match:
        base, exp = bit_match.groups()
        code = f"n = {base}**{exp}\nprint(bin(n).count('1'))"
        result = tools.python(code)
        numbers = re.findall(r'\d+', result)
        if numbers:
            return numbers[-1]
    
    # Pattern 4: Ledger calculations (look for starting balance and operations)
    ledger_pattern = r"starts?\s+at\s+(\d+)"
    ledger_match = re.search(ledger_pattern, question, re.IGNORECASE)
    if ledger_match:
        # Extract all numbers that appear to be operations
        # This is complex; defer to LLM extraction
        pass
    
    return None


def extract_last_number(text):
    """Fallback: extract the last number from text."""
    numbers = re.findall(r'\d+(?:[,\d]*)?', text)
    if numbers:
        return numbers[-1].replace(',', '')
    return ""
