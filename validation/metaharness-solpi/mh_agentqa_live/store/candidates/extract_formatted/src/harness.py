"""
Extract answers using priority: bold-formatted numbers → isolated numbers → last line.
Targets failures where answers are embedded in text or follow verification statements.
"""

import re


def solve(question, llm, tools, files):
    system = files.get("prompts/system.md", "")
    prompt = files["prompts/task.md"].replace("{question}", question)
    reply = llm(prompt, system=system)
    
    # Strategy 1: Extract bold-formatted numbers (highest priority)
    # Matches **number** or **number,number** etc.
    bold_matches = re.findall(r'\*\*(-?[\d,]+(?:\.\d+)?)\*\*', reply)
    if bold_matches:
        # Take the last bold number (usually the final answer)
        last_bold = bold_matches[-1].replace(',', '')
        return last_bold
    
    # Strategy 2: Look for lines containing isolated large numbers or clear statements
    lines = [line.strip() for line in reply.strip().splitlines() if line.strip()]
    for line in reversed(lines):
        # Skip obvious verification/explanation lines
        if any(phrase in line.lower() for phrase in ['verify', 'check', 'confirm', 'calculation check']):
            continue
        
        # Look for standalone numbers (with optional commas)
        numbers = re.findall(r'-?[\d,]+(?:\.\d+)?', line)
        if numbers:
            # Prefer the first substantial number on the line
            candidate = numbers[0].replace(',', '')
            if len(candidate) > 0 and candidate not in ['0', '1']:
                return candidate
    
    # Strategy 3: Fallback to last line (original behavior)
    if lines:
        return lines[-1]
    
    return ""
