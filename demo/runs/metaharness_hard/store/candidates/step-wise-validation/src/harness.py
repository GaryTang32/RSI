"""
Harness combining reasoning with step-wise validation blocks.
Model structures reasoning into explicit steps, each with inline Python verification.
Extracts answer from code output or ANSWER: marker.
"""

import re


def solve(question, llm, tools, files):
    system = files.get("prompts/system.md", "")
    prompt = files.get("prompts/task.md", "").replace("{question}", question)
    reply = llm(prompt, system=system)
    
    # Priority 1: Execute all Python code blocks in order; return last output
    code_pattern = r'```python\n(.*?)\n```'
    code_matches = re.findall(code_pattern, reply, re.DOTALL)
    
    final_code_output = None
    for code in code_matches:
        try:
            execution_result = tools.python(code)
            output_lines = [line.strip() for line in execution_result.strip().split('\n') if line.strip()]
            if output_lines:
                final_code_output = output_lines[-1]
        except Exception:
            # Code execution failed; continue to next block or fallback
            pass
    
    if final_code_output:
        return final_code_output
    
    # Priority 2: Extract ANSWER: marker from text
    for line in reply.strip().splitlines():
        stripped = line.strip()
        if stripped.startswith('ANSWER:'):
            answer_text = stripped[7:].strip()
            if answer_text:
                return answer_text
    
    # Priority 3: Fallback to last non-empty line
    lines = [line for line in reply.strip().splitlines() if line.strip()]
    return lines[-1] if lines else ""
