"""Python-priority extraction: extract values from code-output patterns first."""

import re


def solve(question, llm, tools, files):
    system = files.get("prompts/system.md", "")
    prompt = files["prompts/task.md"].replace("{question}", question)
    reply = llm(prompt, system=system)
    
    return extract_answer(reply)


def extract_answer(text):
    """Extract numeric answer, prioritizing code-output patterns."""
    
    # Strategy 1: Lines with "descriptor: value" pattern (typical Python output)
    # Look for patterns like "result: 144", "sum: 82", "answer: 56", etc.
    # These usually appear early in response (from Python print statements)
    matches = re.findall(
        r'(?:result|sum|total|value|computed|output|answer|digit|final|remainder)[\s\w]*:\s*(-?\d+(?:[.,]\d+)*)',
        text,
        re.IGNORECASE
    )
    if matches:
        # Take the first occurrence (usually from Python output)
        return clean_number(matches[0])
    
    # Strategy 2: Look for "ANSWER: <value>" pattern
    match = re.search(r'ANSWER\s*:\s*(-?\d+(?:[.,]\d+)*)', text, re.IGNORECASE)
    if match:
        return clean_number(match.group(1))
    
    # Strategy 3: Look for bold numbers like **123** - take the last one
    bold_matches = re.findall(r'\*\*(-?\d+(?:[.,]\d+)*)\*\*', text)
    if bold_matches:
        return clean_number(bold_matches[-1])
    
    # Strategy 4: Look for standalone numbers at the end of lines
    lines = text.strip().split('\n')
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        # Extract number at or near end of line
        match = re.search(r'(-?\d+(?:[.,]\d+)*)\s*(?:\)|\.)?$', line)
        if match:
            return clean_number(match.group(1))
    
    # Fallback: return last non-empty line
    for line in reversed(lines):
        if line.strip():
            return line.strip()
    
    return ""


def clean_number(s):
    """Remove commas and spaces from numeric string."""
    return s.replace(',', '').replace(' ', '')
