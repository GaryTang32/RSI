"""Harness with two-stage solving: initial solve, then independent re-verification."""

import re


def solve(question, llm, tools, files):
    system = files.get("prompts/system.md", "")
    
    # Stage 1: Initial solve
    prompt1 = files["prompts/task.md"].replace("{question}", question)
    reply1 = llm(prompt1, system=system)
    
    # Extract candidate answer from first attempt
    answer1 = extract_answer_simple(reply1)
    
    # Stage 2: Re-verification with independent solving
    if answer1:
        prompt2 = files["prompts/verify.md"].format(question=question, previous_answer=answer1)
    else:
        prompt2 = files["prompts/verify.md"].format(question=question, previous_answer="unknown")
    
    reply2 = llm(prompt2, system=system)
    
    # Extract answer from verification stage (should be more reliable)
    answer2 = extract_answer_verification(reply2)
    
    # Return verified answer if available, otherwise first attempt
    return answer2 if answer2 else (answer1 if answer1 else "")


def extract_answer_simple(reply):
    """Quick extraction from first attempt."""
    # Look for ANSWER: line first
    for line in reply.strip().splitlines():
        if "ANSWER:" in line:
            match = re.search(r'ANSWER:\s*([-]?[\d,]+(?:\.\d+)?)', line)
            if match:
                return clean_numeric(match.group(1))
    
    # Then look for "= number" pattern
    lines = reply.strip().splitlines()
    for line in reversed(lines):
        match = re.search(r'=\s*([-]?[\d,]+(?:\.\d+)?)\s*$', line)
        if match:
            return clean_numeric(match.group(1))
    
    # Last resort: last number in last line
    if lines:
        numbers = re.findall(r'[-]?[\d,]+(?:\.\d+)?', lines[-1])
        if numbers:
            return clean_numeric(numbers[-1])
    
    return ""


def extract_answer_verification(reply):
    """Extraction from verification round - expects explicit ANSWER: format."""
    lines = reply.strip().splitlines()
    
    # Verification round should have explicit answer format
    for line in lines:
        if "ANSWER:" in line:
            match = re.search(r'ANSWER:\s*([-]?[\d,]+(?:\.\d+)?)', line)
            if match:
                return clean_numeric(match.group(1))
    
    # Backup: look for "final answer" or similar
    for line in lines:
        if "final" in line.lower() and "answer" in line.lower():
            numbers = re.findall(r'[-]?[\d,]+(?:\.\d+)?', line)
            if numbers:
                return clean_numeric(numbers[-1])
    
    # Last resort
    if lines:
        numbers = re.findall(r'[-]?[\d,]+(?:\.\d+)?', lines[-1])
        if numbers:
            return clean_numeric(numbers[-1])
    
    return ""


def clean_numeric(value):
    """Clean and validate numeric value."""
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
