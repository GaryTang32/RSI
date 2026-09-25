"""
Code-first solving: mandate Python code generation, execute it, extract result.
Bypasses model text reasoning; ensures answers come from computation.
"""

import re


def solve(question, llm, tools, files):
    system = files.get("prompts/system.md", "")
    
    # Prompt model to write Python code that solves and prints the answer
    code_prompt = f"""Solve this problem by writing Python code:

{question}

Write Python code that:
1. Computes the answer
2. Prints it with: print(ANSWER)

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
        
        # Extract numbers from output (last number is most likely the answer)
        numbers = re.findall(r'-?[\d,]+(?:\.\d+)?', output)
        if numbers:
            # Take the last substantial number
            result = numbers[-1].replace(',', '')
            return result
    except Exception:
        # If code execution fails, fall back to text-based solve
        pass
    
    # Fallback: solve via text reasoning like seed
    fallback_prompt = files["prompts/task.md"].replace("{question}", question)
    reply = llm(fallback_prompt, system=system)
    lines = [line for line in reply.strip().splitlines() if line.strip()]
    return lines[-1] if lines else ""
