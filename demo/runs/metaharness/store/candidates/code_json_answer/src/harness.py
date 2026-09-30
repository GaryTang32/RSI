"""Harness that executes Python code and extracts answers from JSON-formatted output."""

import re
import json


def solve(question, llm, tools, files):
    system = """You are an expert problem solver. For the given problem, write Python code that solves it completely.

Your Python code MUST:
1. Solve the problem end-to-end
2. Output the final answer as JSON: print(json.dumps({"answer": <final_answer>}))
3. Be self-contained and executable

Write your code in a ```python code block. You may explain your approach before the code, but focus on producing correct output."""
    
    prompt = files["prompts/task.md"].replace("{question}", question)
    reply = llm(prompt, system=system)
    
    # Extract Python code block
    code_match = re.search(r'```python\n(.*?)\n```', reply, re.DOTALL)
    
    if code_match:
        code = code_match.group(1)
        try:
            # Execute the code using the tool
            exec_result = tools.python(code)
            
            # Strategy 1: Try to parse JSON from output
            for line in reversed(exec_result.split('\n')):
                line = line.strip()
                if line.startswith('{'):
                    try:
                        data = json.loads(line)
                        if 'answer' in data:
                            # Found JSON with answer key
                            answer = data['answer']
                            return str(answer) if answer is not None else ""
                    except (json.JSONDecodeError, TypeError):
                        pass
            
            # Strategy 2: Fall back to ANSWER: marker (for robustness on legacy/unformatted output)
            for line in reversed(exec_result.split('\n')):
                line = line.strip()
                if line.startswith('ANSWER:'):
                    return line.replace('ANSWER:', '').strip()
            
            # Strategy 3: Last non-empty line (catch-all)
            for line in reversed(exec_result.split('\n')):
                if line.strip():
                    return line.strip()
        except Exception:
            # If code execution fails, fall through to LLM fallback
            pass
    
    # Fallback: extract from last line of LLM reply
    lines = [line.strip() for line in reply.split('\n') if line.strip()]
    return lines[-1] if lines else ""
