"""Harness that executes Python code with minimal system prompt."""

import re


def solve(question, llm, tools, files):
    # Minimal system prompt to reduce token usage
    system = "Write Python code to solve this. End with: print('ANSWER: <result>')"
    
    prompt = files["prompts/task.md"].replace("{question}", question)
    reply = llm(prompt, system=system)
    
    # Extract Python code block
    code_match = re.search(r'```python\n(.*?)\n```', reply, re.DOTALL)
    
    if code_match:
        code = code_match.group(1)
        try:
            # Execute the code using the tool
            exec_result = tools.python(code)
            
            # Extract answer from execution output
            # First priority: lines starting with ANSWER:
            for line in reversed(exec_result.split('\n')):
                line = line.strip()
                if line.startswith('ANSWER:'):
                    return line.replace('ANSWER:', '').strip()
            
            # Fallback: return last non-empty line from execution
            for line in reversed(exec_result.split('\n')):
                if line.strip():
                    return line.strip()
        except Exception:
            # If code execution fails, fall through to LLM fallback
            pass
    
    # Fallback: extract from last line of LLM reply if code execution unavailable
    lines = [line.strip() for line in reply.split('\n') if line.strip()]
    return lines[-1] if lines else ""
