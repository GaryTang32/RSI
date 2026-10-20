"""Gentle Python trust: soft guidance that Python is authoritative, minimal structure."""

import re


def solve(question, llm, tools, files):
    system = files.get("prompts/system.md", "")
    prompt = files["prompts/task.md"].replace("{question}", question)
    reply = llm(prompt, system=system)
    
    return extract_answer(reply)


def extract_answer(text):
    """Extract numeric answer using multiple strategies."""
    
    # Strategy 1: Look for "ANSWER: <value>" pattern (most reliable)
    match = re.search(r'ANSWER\s*:\s*(-?\d+(?:[.,]\d+)*)', text, re.IGNORECASE)
    if match:
        return clean_number(match.group(1))
    
    # Strategy 2: Look for bold numbers like **123** - take the last one
    bold_matches = re.findall(r'\*\*(-?\d+(?:[.,]\d+)*)\*\*', text)
    if bold_matches:
        return clean_number(bold_matches[-1])
    
    # Strategy 3: Look for standalone numbers at the end of lines
    lines = text.strip().split('\n')
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        # Extract number at or near end of line (allow some trailing text)
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
