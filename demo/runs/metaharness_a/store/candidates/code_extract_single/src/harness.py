"""Single-stage: model writes Python, shows output, then extracts to ANSWER format."""

import re


def solve(question, llm, tools, files):
    system = files.get("prompts/system.md", "")
    prompt = files["prompts/task.md"].replace("{question}", question)
    reply = llm(prompt, system=system)
    
    answer = extract_model_answer(reply)
    return answer if answer else ""


def extract_model_answer(reply):
    """Extract ANSWER: <number> that model extracted from its own code output."""
    lines = reply.strip().splitlines()
    
    # Priority 1: Strict ANSWER: <number> format on its own line
    for line in lines:
        match = re.search(r'^ANSWER:\s*([-]?[\d.]+)\s*$', line.strip())
        if match:
            value = match.group(1)
            if is_valid_number(value):
                if '.' in value and value.endswith('.0'):
                    return value[:-2]
                return value
    
    # Priority 2: ANSWER: format with potentially more text (model's explanation)
    for line in lines:
        if "ANSWER:" in line:
            match = re.search(r'ANSWER:\s*([-]?[\d.]+)', line)
            if match:
                value = match.group(1)
                if is_valid_number(value):
                    if '.' in value and value.endswith('.0'):
                        return value[:-2]
                    return value
    
    # Priority 3: "= <number>" at end of line (might appear in code output summary)
    for line in reversed(lines):
        match = re.search(r'=\s*([-]?[\d.]+)\s*$', line)
        if match:
            value = match.group(1)
            if is_valid_number(value):
                if '.' in value and value.endswith('.0'):
                    return value[:-2]
                return value
    
    # Priority 4: Last number in last line
    if lines:
        numbers = re.findall(r'[-]?[\d.]+', lines[-1])
        if numbers:
            value = numbers[-1]
            if is_valid_number(value):
                if '.' in value and value.endswith('.0'):
                    return value[:-2]
                return value
    
    return ""


def is_valid_number(value):
    """Check if string is a valid number."""
    if not value:
        return False
    try:
        if '.' in value:
            float(value)
        else:
            int(value)
        return True
    except ValueError:
        return False
