"""Harness that requires approach statement before code generation."""

import re


def solve(question, llm, tools, files):
    system = """You are an expert problem solver. For any problem, first state your approach, then write Python code.

Format your response as:

Approach: [Brief description of strategy, 1-2 sentences]

Then provide Python code in a ```python code block. Your code must:
1. Solve the problem end-to-end
2. End with: print(f"ANSWER: {final_answer}")
3. Be self-contained and executable

The approach helps validate your logic; the code executes it."""
    
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
