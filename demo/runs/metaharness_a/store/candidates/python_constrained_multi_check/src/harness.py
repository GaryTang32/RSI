"""Multi-stage solve with strict Python-first computation and tool-output-aware extraction."""

import re


def solve(question, llm, tools, files):
    system = files.get("prompts/system.md", "")
    
    # Stage 1: Initial Python-first solve
    prompt1 = files["prompts/task.md"].replace("{question}", question)
    reply1 = llm(prompt1, system=system)
    answer1 = extract_from_python_output(reply1)
    if not answer1:
        answer1 = extract_from_text(reply1)
    
    # Stage 2: Independent re-verification with Python requirement
    if answer1:
        prompt2 = files["prompts/verify.md"].format(question=question, previous_answer=answer1)
    else:
        prompt2 = files["prompts/verify.md"].format(question=question, previous_answer="unknown")
    
    reply2 = llm(prompt2, system=system)
    answer2 = extract_from_python_output(reply2)
    if not answer2:
        answer2 = extract_from_text(reply2)
    
    # Return verified answer, fallback to first attempt
    return answer2 if answer2 else (answer1 if answer1 else "")


def extract_from_python_output(reply):
    """Extract answer from Python code output (highest priority)."""
    # Look for function_calls/invoke blocks with bash executing Python
    # Format: <invoke name="bash"><parameter name="command">python3 << 'EOF'...EOF</parameter></invoke>
    
    # Strategy 1: Look for explicit print() outputs near code blocks
    # Match patterns like "print(...123...)" or numbers shown after EOF
    
    # Extract content between code markers
    eof_pattern = r"EOF\s*\n(.*?)(?:\n\s*<|$)"
    eof_blocks = re.findall(eof_pattern, reply, re.DOTALL)
    
    for block in eof_blocks:
        # Look for numbers in output that appear to be results
        numbers = re.findall(r'\n\s*([-]?[\d,]+(?:\.\d+)?)\s*$', block, re.MULTILINE)
        if numbers:
            # Take last/most relevant number from output block
            return clean_numeric(numbers[-1])
    
    # Strategy 2: Look for ``` python ``` code blocks
    python_blocks = re.findall(r'```(?:python|py)\s*(.*?)```', reply, re.DOTALL)
    for block in reversed(python_blocks):
        # Extract numbers that appear to be outputs
        numbers = re.findall(r'=\s*([-]?[\d,]+(?:\.\d+)?)\s*$', block, re.MULTILINE)
        if numbers:
            return clean_numeric(numbers[-1])
    
    # Strategy 3: Look for "= number" assignments in code
    all_equals = re.findall(r'=\s*([-]?[\d,]+(?:\.\d+)?)\s*(?:\n|$)', reply)
    if all_equals:
        return clean_numeric(all_equals[-1])
    
    return ""


def extract_from_text(reply):
    """Extract answer from text-based patterns (fallback)."""
    lines = reply.strip().splitlines()
    
    # Priority 1: ANSWER: <value> format
    for line in lines:
        if "ANSWER:" in line:
            match = re.search(r'ANSWER:\s*([-]?[\d,]+(?:\.\d+)?)', line)
            if match:
                return clean_numeric(match.group(1))
    
    # Priority 2: "= number" at end of line
    for line in reversed(lines):
        match = re.search(r'=\s*([-]?[\d,]+(?:\.\d+)?)\s*$', line)
        if match:
            return clean_numeric(match.group(1))
    
    # Priority 3: **number** (bold format)
    for line in reversed(lines):
        match = re.search(r'\*\*\s*([-]?[\d,]+(?:\.\d+)?)\s*\*\*', line)
        if match:
            return clean_numeric(match.group(1))
    
    # Priority 4: Last number in last line
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
