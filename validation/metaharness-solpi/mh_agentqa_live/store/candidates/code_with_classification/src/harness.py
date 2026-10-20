"""
Code-first solving with problem classification.
Model classifies problem type and computational approach before writing code.
"""

import re


def solve(question, llm, tools, files):
    system = files.get("prompts/system.md", "")
    
    # Step 1: Ask model to classify problem and identify approach
    classify_prompt = f"""Analyze this problem and identify:
1. Problem type (e.g., modular arithmetic, text manipulation, date arithmetic, counting, string search, etc.)
2. What computational operations or algorithms are needed
3. Expected output type (number, string, date, list, etc.)

Problem: {question}

Provide a brief analysis (1-2 sentences on approach, then state: "Solution approach: <method>")"""
    
    classify_response = llm(classify_prompt, system=system)
    
    # Step 2: Now write code, informed by the classification
    code_prompt = f"""Based on this analysis:

{classify_response}

Write Python code that solves this problem:

{question}

Write Python code that:
1. Computes the answer correctly
2. Prints the final result clearly

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
        
        # Extract answer from output
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
    Extract answer from code output.
    Handles: numbers, strings, dates, lists.
    """
    output = output.strip()
    if not output:
        return ""
    
    lines = [line.strip() for line in output.split('\n') if line.strip()]
    if not lines:
        return ""
    
    last_line = lines[-1]
    
    # Strategy 1: Numbers (highest priority for numeric problems)
    numbers = re.findall(r'-?[\d,]+(?:\.\d+)?', last_line)
    if numbers:
        return numbers[-1].replace(',', '')
    
    # Strategy 2: Quoted strings
    quoted_match = re.search(r'''["']([^"']+)["']''', last_line)
    if quoted_match:
        return quoted_match.group(1)
    
    # Strategy 3: Dates
    date_match = re.search(r'\d{4}-\d{2}-\d{2}', last_line)
    if date_match:
        return date_match.group(0)
    
    # Strategy 4: Lists
    list_match = re.search(r'(\[[^\]]+\])', last_line)
    if list_match:
        return list_match.group(1)
    
    # Strategy 5: Return entire last line
    return last_line
