import re

def solve(question, llm, tools, files):
    """
    Self-critique code harness: Generate code, ask the model to critique it for bugs,
    optionally regenerate if issues are found, then execute. This two-stage LLM + one-stage
    tool approach improves robustness by catching code generation errors before execution.
    """
    system = files.get("prompts/system.md", "")
    prompt_template = files["prompts/task.md"]
    prompt = prompt_template.replace("{question}", question)
    
    # ===== STAGE 1: Generate code =====
    generation_prompt = f"""{prompt}

Please solve this problem by providing Python code in a ```python code block.
The code must output ONLY the final numerical answer (one line, no text)."""
    
    reply1 = llm(generation_prompt, system=system)
    
    # Extract first code block
    code_match = re.search(r'```(?:python)?\s*\n(.*?)\n```', reply1, re.DOTALL)
    if not code_match:
        # Fallback: try to find ANSWER: marker or return last line
        for line in reply1.strip().splitlines():
            line_stripped = line.strip()
            if line_stripped.startswith("ANSWER:"):
                return line_stripped[len("ANSWER:"):].strip()
        lines = [l for l in reply1.strip().splitlines() if l.strip()]
        return lines[-1] if lines else ""
    
    code = code_match.group(1)
    
    # ===== STAGE 2: Self-critique the code =====
    critique_prompt = f"""{prompt}

I wrote this Python code to solve the problem:

```python
{code}
```

Please review this code for:
1. Logic correctness (does it solve the problem?)
2. Potential bugs or edge cases
3. Syntax errors or missing imports

If you find any issues, provide corrected code in a ```python code block.
If the code looks correct, simply state "Code is correct." (no code block needed).

Be concise."""
    
    reply2 = llm(critique_prompt, system=system)
    
    # Check if model provided corrected code
    corrected_match = re.search(r'```(?:python)?\s*\n(.*?)\n```', reply2, re.DOTALL)
    if corrected_match:
        code = corrected_match.group(1)
    # else: use original code (model said it was correct or couldn't fix)
    
    # ===== STAGE 3: Execute the (original or corrected) code =====
    try:
        output = tools.python(code)
        lines = [l.strip() for l in output.strip().splitlines() if l.strip()]
        if lines:
            return lines[-1]
    except Exception:
        pass
    
    # Fallback: Look for ANSWER: marker in critique response
    for line in reply2.strip().splitlines():
        line_stripped = line.strip()
        if line_stripped.startswith("ANSWER:"):
            answer = line_stripped[len("ANSWER:"):].strip()
            if answer:
                return answer
    
    # Last resort: return last non-empty line from critique
    lines = [l for l in reply2.strip().splitlines() if l.strip()]
    return lines[-1] if lines else ""
