"""
Harness combining reasoning and code: encourages both, extracts from code OR ANSWER: line.
"""

import re


def solve(question, llm, tools, files):
    system = files.get("prompts/system.md", "")
    prompt = files["prompts/task.md"].replace("{question}", question)
    reply = llm(prompt, system=system)
    
    # Priority 1: Execute Python code blocks if present
    code_pattern = r'```python\n(.*?)\n```'
    code_matches = re.findall(code_pattern, reply, re.DOTALL)
    
    for code in code_matches:
        try:
            execution_result = tools.python(code)
            output_lines = [line.strip() for line in execution_result.strip().split('\n') if line.strip()]
            if output_lines:
                return output_lines[-1]
        except Exception:
            # Code execution failed; continue to next fallback
            pass
    
    # Priority 2: Extract ANSWER: line (explicit marker from prompt)
    for line in reply.strip().splitlines():
        stripped = line.strip()
        if stripped.startswith('ANSWER:'):
            answer_text = stripped[7:].strip()
            if answer_text:
                return answer_text
    
    # Priority 3: Fallback to last non-empty line
    lines = [line for line in reply.strip().splitlines() if line.strip()]
    return lines[-1] if lines else ""
