"""Ensemble approach: three independent solves with majority-vote answer selection."""

import re
from collections import Counter


def solve(question, llm, tools, files):
    system = files.get("prompts/system.md", "")
    prompt_template = files["prompts/task.md"]
    
    answers = []
    
    # Run three independent solves
    for attempt in range(3):
        prompt = prompt_template.replace("{question}", question)
        reply = llm(prompt, system=system)
        answer = extract_answer(reply)
        if answer:
            answers.append(answer)
    
    # Select answer by majority vote
    if not answers:
        return ""
    
    # Count occurrences of each answer
    counter = Counter(answers)
    most_common = counter.most_common(1)
    
    if most_common:
        # Return the most frequently occurring answer
        return most_common[0][0]
    
    # Fallback: return first answer if no consensus
    return answers[0] if answers else ""


def extract_answer(reply):
    """Extract numeric answer from reply using multi-strategy approach."""
    lines = reply.strip().splitlines()
    
    if not lines:
        return ""
    
    # Strategy 1: Look for ANSWER: <value> line
    for line in lines:
        if "ANSWER:" in line:
            match = re.search(r'ANSWER:\s*([-]?[\d,]+(?:\.\d+)?)', line)
            if match:
                return clean_numeric(match.group(1))
    
    # Strategy 2: Look for "= <number>" at end of line
    for line in reversed(lines):
        match = re.search(r'=\s*([-]?[\d,]+(?:\.\d+)?)\s*$', line)
        if match:
            return clean_numeric(match.group(1))
    
    # Strategy 3: Look for bold markup around number
    for line in reversed(lines):
        match = re.search(r'\*\*\s*([-]?[\d,]+(?:\.\d+)?)\s*\*\*', line)
        if match:
            return clean_numeric(match.group(1))
    
    # Strategy 4: Extract last number found in last line
    if lines:
        last = lines[-1]
        numbers = re.findall(r'[-]?[\d,]+(?:\.\d+)?', last)
        if numbers:
            return clean_numeric(numbers[-1])
    
    return ""


def clean_numeric(value):
    """Clean numeric value by removing formatting characters."""
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
