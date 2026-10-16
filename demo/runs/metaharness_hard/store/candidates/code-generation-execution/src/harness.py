"""Harness with code generation and execution for data-intensive problems."""

import re


def solve(question, llm, tools, files):
    system = files.get("prompts/system.md", "")
    prompt = files["prompts/task.md"].replace("{question}", question)
    reply = llm(prompt, system=system)
    
    # Step 1: Try to find and execute Python code blocks
    code_pattern = r'```python\n(.*?)\n```'
    code_matches = re.findall(code_pattern, reply, re.DOTALL)
    
    for code in code_matches:
        try:
            # Execute the Python code using the provided tools
            execution_result = tools.python(code)
            # Extract the last non-empty line from execution output
            output_lines = [line.strip() for line in execution_result.strip().split('\n') if line.strip()]
            if output_lines:
                # Return the last output line as the answer
                return output_lines[-1]
        except Exception:
            # If code execution fails, continue to next code block or fall back to text extraction
            pass
    
    # Step 2: Fall back to extracting text answer
    # First, look for ANSWER: marker in the response
    for line in reply.strip().splitlines():
        stripped = line.strip()
        if stripped.startswith('ANSWER:'):
            return stripped[7:].strip()
    
    # Last resort: return the last non-empty line
    lines = [line for line in reply.strip().splitlines() if line.strip()]
    return lines[-1] if lines else ""
