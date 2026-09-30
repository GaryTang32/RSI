"""Harness that decomposes the problem before generating code."""

import re


def solve(question, llm, tools, files):
    system = """You are an expert problem solver. For any problem, follow this process:

1. Analyze and decompose the problem:
   - Identify the problem type (numeric, date, list, text, logic, etc.)
   - Describe your solution approach
   - List potential edge cases or pitfalls

2. Then write Python code that solves it:
   - End with a line that prints: ANSWER: <final_answer>
   - Be self-contained and executable

Format your response as:

<ANALYSIS>
problem_type: [type]
approach: [your strategy in 1-2 sentences]
edge_cases: [list potential issues]
</ANALYSIS>

Then provide your Python code in a ```python code block."""
    
    prompt = files["prompts/task.md"].replace("{question}", question)
    reply = llm(prompt, system=system)
    
    # Extract and store analysis (for harness-level memory/debugging)
    analysis_match = re.search(r'<ANALYSIS>(.*?)</ANALYSIS>', reply, re.DOTALL)
    analysis = ""
    if analysis_match:
        analysis = analysis_match.group(1).strip()
    
    # Extract Python code block
    code_match = re.search(r'```python\n(.*?)\n```', reply, re.DOTALL)
    
    if code_match:
        code = code_match.group(1)
        try:
            # Execute the code using the tool
            exec_result = tools.python(code)
            
            # Extract answer from execution output
            # First priority: ANSWER: marker
            for line in reversed(exec_result.split('\n')):
                line = line.strip()
                if line.startswith('ANSWER:'):
                    return line.replace('ANSWER:', '').strip()
            
            # Fallback: last non-empty line
            for line in reversed(exec_result.split('\n')):
                if line.strip():
                    return line.strip()
        except Exception:
            # If code execution fails, fall through to LLM fallback
            pass
    
    # Fallback: extract from last line of LLM reply
    lines = [line.strip() for line in reply.split('\n') if line.strip()]
    return lines[-1] if lines else ""
