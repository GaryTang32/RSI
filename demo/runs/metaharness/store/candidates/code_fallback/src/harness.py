"""Harness that executes Python code with multi-path answer extraction."""

import re


def solve(question, llm, tools, files):
    system = """You are an expert problem solver. For the given problem, write Python code that solves it completely.

Your Python code MUST:
1. Solve the problem end-to-end
2. End with a line that prints: ANSWER: <final_answer>

Write your code in a ```python code block. You may explain your approach, but the code must be self-contained and executable.

After the code block, do not modify or restate the answer—let the code output speak."""
    
    prompt = files["prompts/task.md"].replace("{question}", question)
    reply = llm(prompt, system=system)
    
    # Extract Python code block
    code_match = re.search(r'```python\n(.*?)\n```', reply, re.DOTALL)
    
    if code_match:
        code = code_match.group(1)
        try:
            # Execute the code using the tool
            exec_result = tools.python(code)
            
            # Strategy 1: Extract answer from ANSWER: marker (primary)
            for line in reversed(exec_result.split('\n')):
                line = line.strip()
                if line.startswith('ANSWER:'):
                    return line.replace('ANSWER:', '').strip()
            
            # Strategy 2: Last non-empty line (secondary fallback)
            for line in reversed(exec_result.split('\n')):
                if line.strip():
                    answer = line.strip()
                    # Filter: if last line looks like an answer (numeric, short text), return it
                    # This handles cases where code prints result without ANSWER: prefix
                    return answer
        except Exception:
            # If code execution fails, fall through to LLM fallback
            pass
    
    # Fallback: extract from last line of LLM reply if code execution unavailable
    lines = [line.strip() for line in reply.split('\n') if line.strip()]
    return lines[-1] if lines else ""
