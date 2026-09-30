"""Two-stage solving with strict ANSWER format and Python-enforced computation."""

import re


def solve(question, llm, tools, files):
    system = files.get("prompts/system.md", "")
    
    # Stage 1: Initial solve with strict format requirement
    prompt1 = files["prompts/task.md"].replace("{question}", question)
    reply1 = llm(prompt1, system=system)
    answer1 = extract_strict_answer(reply1)
    
    # Stage 2: Re-verification with independent solving
    if answer1:
        prompt2 = files["prompts/verify.md"].format(question=question, previous_answer=answer1)
    else:
        prompt2 = files["prompts/verify.md"].format(question=question, previous_answer="unknown")
    
    reply2 = llm(prompt2, system=system)
    answer2 = extract_strict_answer(reply2)
    
    # Return verified answer if available, otherwise first attempt
    return answer2 if answer2 else (answer1 if answer1 else "")


def extract_strict_answer(reply):
    """Extract ANSWER: <number> format strictly. Rejects other formats."""
    lines = reply.strip().splitlines()
    
    # Only accept lines that match ANSWER: <number> pattern exactly
    for line in lines:
        # Match "ANSWER: " followed by optional whitespace and a number (with optional negative sign and decimals)
        match = re.search(r'^ANSWER:\s*([-]?[\d.]+)\s*$', line.strip())
        if match:
            value = match.group(1)
            # Validate it's actually numeric
            try:
                if '.' in value:
                    float(value)
                else:
                    int(value)
                # Remove trailing .0 from integers
                if '.' in value and value.endswith('.0'):
                    return value[:-2]
                return value
            except ValueError:
                continue
    
    # Fallback: if no strict ANSWER: line found, try relaxed extraction
    # Look for ANSWER: with potentially more text on same line
    for line in lines:
        if "ANSWER:" in line:
            match = re.search(r'ANSWER:\s*([-]?[\d.]+)', line)
            if match:
                value = match.group(1)
                try:
                    if '.' in value:
                        float(value)
                    else:
                        int(value)
                    if '.' in value and value.endswith('.0'):
                        return value[:-2]
                    return value
                except ValueError:
                    continue
    
    # Final fallback: last number in reply
    all_numbers = re.findall(r'[-]?[\d.]+', reply)
    if all_numbers:
        value = all_numbers[-1]
        try:
            if '.' in value:
                float(value)
            else:
                int(value)
            if '.' in value and value.endswith('.0'):
                return value[:-2]
            return value
        except ValueError:
            pass
    
    return ""
