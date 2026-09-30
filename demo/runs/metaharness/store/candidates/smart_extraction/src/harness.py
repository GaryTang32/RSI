"""Harness with intelligent answer extraction handling multiple format variations."""

import re


def solve(question, llm, tools, files):
    system = files.get("prompts/system.md", "")
    prompt = files["prompts/task.md"].replace("{question}", question)
    reply = llm(prompt, system=system)
    
    answer = extract_answer(reply)
    return answer if answer else ""


def extract_answer(reply):
    """Extract numeric answer from LLM reply, handling various formats."""
    lines = reply.strip().splitlines()
    
    if not lines:
        return ""
    
    # Strategy 1: Look for ANSWER: <value> line
    for line in lines:
        if "ANSWER:" in line:
            match = re.search(r'ANSWER:\s*([-]?[\d,]+(?:\.\d+)?)', line)
            if match:
                value = match.group(1)
                cleaned = clean_numeric(value)
                if cleaned:
                    return cleaned
    
    # Strategy 2: Look for "= <number>" at end of line
    for line in reversed(lines):
        match = re.search(r'=\s*([-]?[\d,]+(?:\.\d+)?)\s*$', line)
        if match:
            value = match.group(1)
            cleaned = clean_numeric(value)
            if cleaned:
                return cleaned
    
    # Strategy 3: Look for bold/emphasis markup around number
    for line in reversed(lines):
        # Match **number** or similar
        match = re.search(r'\*\*\s*([-]?[\d,]+(?:\.\d+)?)\s*\*\*', line)
        if match:
            value = match.group(1)
            cleaned = clean_numeric(value)
            if cleaned:
                return cleaned
    
    # Strategy 4: Extract last number found in last line
    if lines:
        last = lines[-1]
        numbers = re.findall(r'[-]?[\d,]+(?:\.\d+)?', last)
        if numbers:
            value = numbers[-1]
            cleaned = clean_numeric(value)
            if cleaned:
                return cleaned
    
    # Fallback: return last line as-is
    return lines[-1] if lines else ""


def clean_numeric(value):
    """Clean numeric value by removing formatting characters."""
    if not value:
        return ""
    
    # Remove commas (thousands separator)
    value = value.replace(",", "")
    # Remove extra spaces
    value = value.strip()
    
    # Validate it parses as a number
    try:
        if "." in value:
            float(value)
        else:
            int(value)
        return value
    except ValueError:
        return ""
