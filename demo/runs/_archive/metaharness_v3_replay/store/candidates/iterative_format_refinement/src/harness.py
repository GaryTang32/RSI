"""Iterative refinement harness: retry if initial extraction fails."""

import re


def solve(question, llm, tools, files):
    system = files.get("prompts/system.md", "")
    prompt = files["prompts/task.md"].replace("{question}", question)
    
    # First attempt
    reply = llm(prompt, system=system)
    answer = extract_answer(reply)
    
    # If no clear numeric answer found, retry with explicit format request
    if not answer:
        format_prompt = (
            "Based on your work above, please provide your final answer in this exact format:\n\n"
            "ANSWER: <numeric value>\n\n"
            "If you had any intermediate results, verify them and state the correct numeric answer."
        )
        retry_reply = llm(format_prompt, system=system)
        answer = extract_answer(retry_reply)
    
    # Final fallback: last non-empty line from initial attempt
    if not answer:
        lines = [line for line in reply.strip().splitlines() if line.strip()]
        return lines[-1] if lines else ""
    
    return answer


def extract_answer(text):
    """Extract numeric answer using multiple strategies."""
    
    # Strategy 1: "ANSWER: <value>"
    match = re.search(r'ANSWER\s*:\s*(-?\d+(?:[.,]\d+)*)', text, re.IGNORECASE)
    if match:
        return clean_number(match.group(1))
    
    # Strategy 2: Bold numbers
    bold_matches = re.findall(r'\*\*(-?\d+(?:[.,]\d+)*)\*\*', text)
    if bold_matches:
        return clean_number(bold_matches[-1])
    
    # Strategy 3: Line-ending numbers
    lines = text.strip().split('\n')
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        match = re.search(r'(-?\d+(?:[.,]\d+)*)\s*(?:\)|\.)?$', line)
        if match:
            return clean_number(match.group(1))
    
    return None


def clean_number(s):
    """Remove commas and spaces from numeric string."""
    return s.replace(',', '').replace(' ', '')
