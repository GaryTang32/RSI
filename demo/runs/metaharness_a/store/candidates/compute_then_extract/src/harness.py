"""Harness that enforces Python computation and extracts from code output."""

import re


def solve(question, llm, tools, files):
    system = files.get("prompts/system.md", "")
    prompt = files["prompts/task.md"].replace("{question}", question)
    reply = llm(prompt, system=system)
    
    answer = extract_answer_from_computation(reply)
    return answer if answer else ""


def extract_answer_from_computation(reply):
    """Extract numeric answer prioritizing Python code output over LLM text."""
    
    # Strategy 1: Extract from Python print statements or code output
    answer = extract_from_python_output(reply)
    if answer:
        return answer
    
    # Strategy 2: Look for ANSWER: <value> line
    answer = extract_from_answer_line(reply)
    if answer:
        return answer
    
    # Strategy 3: Look for "= <number>" pattern (result of calculation)
    answer = extract_from_equals(reply)
    if answer:
        return answer
    
    # Strategy 4: Extract last number in last line
    lines = reply.strip().splitlines()
    if lines:
        answer = extract_last_number(lines[-1])
        if answer:
            return answer
    
    return ""


def extract_from_python_output(reply):
    """Extract numbers from Python code blocks or print outputs."""
    # Look for markdown code blocks with python
    code_block_pattern = r'```(?:python|py)?\s*(.*?)```'
    blocks = re.findall(code_block_pattern, reply, re.DOTALL)
    
    if blocks:
        # Process last code block first
        for block in reversed(blocks):
            # Look for print() calls and their implicit outputs
            # Match: print(f"... {num}") or print(num)
            print_matches = re.findall(r'print\s*\(\s*[^)]*?([-]?[\d,]+(?:\.\d+)?)[^)]*?\)', block)
            if print_matches:
                value = print_matches[-1]
                cleaned = clean_numeric(value)
                if cleaned:
                    return cleaned
    
    # Also look for function_calls format (Claude's format)
    func_call_pattern = r'<invoke name="(?:python|bash)"[^>]*>(.*?)</invoke>'
    invokes = re.findall(func_call_pattern, reply, re.DOTALL)
    
    for invoke in reversed(invokes):
        # Look for output lines with numbers
        lines = invoke.strip().splitlines()
        for line in reversed(lines):
            numbers = re.findall(r'[-]?[\d,]+(?:\.\d+)?', line)
            if numbers:
                value = numbers[-1]
                cleaned = clean_numeric(value)
                if cleaned:
                    return cleaned
    
    return ""


def extract_from_answer_line(reply):
    """Extract from ANSWER: <value> format."""
    for line in reply.strip().splitlines():
        if "ANSWER:" in line:
            match = re.search(r'ANSWER:\s*([-]?[\d,]+(?:\.\d+)?)', line)
            if match:
                return clean_numeric(match.group(1))
    return ""


def extract_from_equals(reply):
    """Extract from '= <number>' at end of line."""
    for line in reversed(reply.strip().splitlines()):
        match = re.search(r'=\s*([-]?[\d,]+(?:\.\d+)?)\s*$', line)
        if match:
            return clean_numeric(match.group(1))
    return ""


def extract_last_number(text):
    """Extract the last number found in text."""
    numbers = re.findall(r'[-]?[\d,]+(?:\.\d+)?', text)
    if numbers:
        return clean_numeric(numbers[-1])
    return ""


def clean_numeric(value):
    """Clean numeric value for parsing."""
    if not value:
        return ""
    value = value.replace(",", "").strip()
    try:
        if "." in value:
            float(value)
        else:
            int(value)
        return value
    except ValueError:
        return ""
