"""
Code-first solving with generalized output extraction.
Removes math-specific language; handles numeric, string, date, and list outputs.
"""

import re


def solve(question, llm, tools, files):
    system = files.get("prompts/system.md", "")
    
    # Prompt model to write Python code that solves and prints the answer
    code_prompt = f"""Solve this problem by writing Python code:

{question}

Write Python code that:
1. Computes the answer
2. Prints the final result (could be a number, string, date, list, etc.)

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
        
        # Try multiple extraction strategies for different output types
        result = _extract_answer(output)
        if result:
            return result
    except Exception:
        # If code execution fails, fall back to text-based solve
        pass
    
    # Fallback: solve via text reasoning
    fallback_prompt = files["prompts/task.md"].replace("{question}", question)
    reply = llm(fallback_prompt, system=system)
    lines = [line for line in reply.strip().splitlines() if line.strip()]
    return lines[-1] if lines else ""


def _extract_answer(output):
    """
    Extract answer from code output, handling multiple data types.
    Tries: numbers, quoted strings, labeled values, last line.
    """
    output = output.strip()
    if not output:
        return ""
    
    lines = [line.strip() for line in output.split('\n') if line.strip()]
    
    # Strategy 1: Look for standalone numbers (most common for numeric problems)
    numbers = re.findall(r'-?[\d,]+(?:\.\d+)?', lines[-1] if lines else "")
    if numbers:
        last_num = numbers[-1].replace(',', '')
        # Return if it looks like a real answer (not just single digit unless expected)
        if len(last_num) > 0:
            return last_num
    
    # Strategy 2: Look for quoted strings (text answers)
    quoted_match = re.search(r'''["']([^"']+)["']''', lines[-1] if lines else "")
    if quoted_match:
        return quoted_match.group(1)
    
    # Strategy 3: Look for dates (YYYY-MM-DD or similar)
    date_match = re.search(r'\d{4}-\d{2}-\d{2}', lines[-1] if lines else "")
    if date_match:
        return date_match.group(0)
    
    # Strategy 4: Look for list/array output
    list_match = re.search(r'(\[[^\]]+\])', lines[-1] if lines else "")
    if list_match:
        return list_match.group(1)
    
    # Strategy 5: Return entire last line as fallback
    if lines:
        return lines[-1]
    
    return ""
