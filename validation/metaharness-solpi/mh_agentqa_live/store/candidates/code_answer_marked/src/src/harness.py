"""
Code-first with explicit ANSWER= format.
Enforces structured output so extraction is unambiguous.
"""

import re


def solve(question, llm, tools, files):
    system = files.get("prompts/system.md", "")
    
    # Prompt model to write Python code that outputs answer in strict format
    code_prompt = f"""Solve this problem by writing Python code:

{question}

Write Python code that:
1. Computes the answer
2. On the final line, prints: ANSWER=<value>

The last line MUST be: ANSWER=<computed_result>

Provide ONLY the Python code, no explanation."""
    
    code_response = llm(code_prompt, system=system)
    
    # Extract Python code (remove markdown wrappers if present)
    code = code_response
    for wrapper in ['```python\n', '```python', '```\n', '```']:
        code = code.replace(wrapper, '')
    code = code.rstrip('`').strip()
    
    # Execute the code
    try:
        output = tools.python(code)
        
        # Strategy 1: Look for ANSWER=<value> pattern (highest priority)
        answer_match = re.search(r'ANSWER\s*=\s*(-?[\d,]+(?:\.\d+)?)', output, re.IGNORECASE)
        if answer_match:
            result = answer_match.group(1).replace(',', '')
            return result
        
        # Strategy 2: Fall back to last non-empty line (for cases where ANSWER= format wasn't used)
        lines = [line.strip() for line in output.strip().splitlines() if line.strip()]
        if lines:
            # Try to extract number from last line
            numbers = re.findall(r'-?[\d,]+(?:\.\d+)?', lines[-1])
            if numbers:
                return numbers[-1].replace(',', '')
            # Return entire last line as fallback
            return lines[-1]
    except Exception:
        # If code execution fails, fall back to text-based solve
        pass
    
    # Fallback: solve via text reasoning like seed
    fallback_prompt = files["prompts/task.md"].replace("{question}", question)
    reply = llm(fallback_prompt, system=system)
    lines = [line for line in reply.strip().splitlines() if line.strip()]
    return lines[-1] if lines else ""
