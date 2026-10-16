import re

def solve(question, llm, tools, files):
    """
    Hybrid code-text harness: Request step-by-step reasoning followed by executable code.
    Separates explanation from implementation to improve code extraction and ensure
    arithmetic is delegated to Python rather than attempted in text.
    """
    system = files.get("prompts/system.md", "")
    prompt_template = files["prompts/task.md"]
    prompt = prompt_template.replace("{question}", question)
    
    # Request both reasoning and code with clear separation
    solution_prompt = f"""{prompt}

Please solve this problem using the following structure:

1. Show your step-by-step reasoning and intermediate calculations.
2. Then provide Python code in a ```python code block that outputs ONLY the final numerical answer on a single line.

The Python code must:
- Be syntactically correct and runnable
- Output exactly one line containing only the numerical answer
- Not include any explanatory text or intermediate output"""
    
    reply = llm(solution_prompt, system=system)
    
    # Extract all Python code blocks
    code_blocks = re.findall(r'```(?:python)?\s*\n(.*?)\n```', reply, re.DOTALL)
    
    # Try code blocks in reverse order (prefer the last/final code block)
    if code_blocks:
        for code in reversed(code_blocks):
            try:
                output = tools.python(code)
                # Return last non-empty line of execution output
                lines = [l.strip() for l in output.strip().splitlines() if l.strip()]
                if lines:
                    return lines[-1]
            except Exception:
                # Code block failed; try the previous one
                continue
    
    # Fallback 1: Look for ANSWER: marker in text response
    for line in reply.strip().splitlines():
        line_stripped = line.strip()
        if line_stripped.startswith("ANSWER:"):
            answer = line_stripped[len("ANSWER:"):].strip()
            if answer:
                return answer
    
    # Fallback 2: Return last non-empty line
    lines = [l for l in reply.strip().splitlines() if l.strip()]
    return lines[-1] if lines else ""
