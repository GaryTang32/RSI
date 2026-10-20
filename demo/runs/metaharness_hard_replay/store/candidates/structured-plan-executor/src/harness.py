"""
Harness with two-phase execution: plan outline → implementation.
Phase 1: Model outlines the solution approach.
Phase 2: Model implements the plan with code or reasoning.
Harness validates and executes, extracting the final answer.
"""

import re


def solve(question, llm, tools, files):
    # Phase 1: Get solution plan/outline
    system_plan = files.get("prompts/system_plan.md", "")
    prompt_plan = files.get("prompts/task_plan.md", "").replace("{question}", question)
    plan_reply = llm(prompt_plan, system=system_plan)
    
    # Detect task type from plan content
    plan_lower = plan_reply.lower()
    is_data_task = any(word in plan_lower for word in 
                       ['code', 'python', 'calculate', 'filter', 'parse',
                        'convert', 'average', 'sum', 'data', 'transaction'])
    is_logic_task = any(word in plan_lower for word in
                        ['reason', 'constraint', 'deduc', 'logic', 'eliminat',
                         'clue', 'puzzle', 'step-by-step'])
    
    # Phase 2: Execute plan with appropriate system prompt
    if is_data_task:
        system_exec = files.get("prompts/system_data.md", "")
    else:
        system_exec = files.get("prompts/system_logic.md", "")
    
    prompt_exec = files.get("prompts/task_exec.md", "").replace("{question}", question).replace("{plan}", plan_reply)
    exec_reply = llm(prompt_exec, system=system_exec)
    
    # Extract answer: prioritize code execution, then ANSWER: marker, then fallback
    if is_data_task:
        # For data tasks, try code execution first
        code_pattern = r'```python\n(.*?)\n```'
        code_matches = re.findall(code_pattern, exec_reply, re.DOTALL)
        
        for code in code_matches:
            try:
                execution_result = tools.python(code)
                output_lines = [line.strip() for line in execution_result.strip().split('\n') if line.strip()]
                if output_lines:
                    return output_lines[-1]
            except Exception:
                pass
    
    # Try ANSWER: marker (works for both logic and data)
    for line in exec_reply.strip().splitlines():
        stripped = line.strip()
        if stripped.startswith('ANSWER:'):
            answer_text = stripped[7:].strip()
            if answer_text:
                return answer_text
    
    # Fallback: return last non-empty line
    lines = [line for line in exec_reply.strip().splitlines() if line.strip()]
    return lines[-1] if lines else ""
