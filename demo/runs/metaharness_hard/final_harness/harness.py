"""
Harness with task-type detection and routing to specialized strategies.
- Logic tasks: Reasoning-based with formatted answer requirement
- Data tasks: Code-first with calculation templates
"""

import re


def detect_task_type(question):
    """Detect whether the question is logic-heavy or data-heavy."""
    question_lower = question.lower()
    
    # Keywords indicating data/calculation tasks
    data_keywords = [
        "transaction", "ledger", "average", "sum", "total", "date", "amount",
        "currency", "convert", "rate", "paid", "refund", "filter", "calculate",
        "export", "usd", "eur", "settled", "status", "balance", "accounting"
    ]
    
    # Keywords indicating logic/reasoning tasks
    logic_keywords = [
        "logic puzzle", "clue", "must be", "person", "house", "color", "hobby",
        "pet", "drink", "constraint", "immediately left", "next to", "different",
        "standing in a row", "position"
    ]
    
    data_score = sum(1 for kw in data_keywords if kw in question_lower)
    logic_score = sum(1 for kw in logic_keywords if kw in question_lower)
    
    return "data" if data_score > logic_score else "logic"


def solve(question, llm, tools, files):
    task_type = detect_task_type(question)
    
    if task_type == "data":
        return solve_data(question, llm, tools, files)
    else:
        return solve_logic(question, llm, tools, files)


def solve_data(question, llm, tools, files):
    """Solve data-intensive tasks using code-first approach with templates."""
    system = files.get("prompts/system_data.md", "")
    prompt = files.get("prompts/task_data.md", "").replace("{question}", question)
    reply = llm(prompt, system=system)
    
    # Priority 1: Execute Python code blocks
    code_pattern = r'```python\n(.*?)\n```'
    code_matches = re.findall(code_pattern, reply, re.DOTALL)
    
    for code in code_matches:
        try:
            execution_result = tools.python(code)
            output_lines = [line.strip() for line in execution_result.strip().split('\n') if line.strip()]
            if output_lines:
                return output_lines[-1]
        except Exception:
            pass
    
    # Priority 2: Extract ANSWER: marker
    for line in reply.strip().splitlines():
        stripped = line.strip()
        if stripped.startswith('ANSWER:'):
            answer_text = stripped[7:].strip()
            if answer_text:
                return answer_text
    
    # Priority 3: Last line fallback
    lines = [line for line in reply.strip().splitlines() if line.strip()]
    return lines[-1] if lines else ""


def solve_logic(question, llm, tools, files):
    """Solve logic tasks using step-by-step reasoning with formatted answer."""
    system = files.get("prompts/system_logic.md", "")
    prompt = files.get("prompts/task_logic.md", "").replace("{question}", question)
    reply = llm(prompt, system=system)
    
    # Priority 1: Extract ANSWER: marker (preferred for logic)
    for line in reply.strip().splitlines():
        stripped = line.strip()
        if stripped.startswith('ANSWER:'):
            answer_text = stripped[7:].strip()
            if answer_text:
                return answer_text
    
    # Priority 2: Execute code if present (optional for logic)
    code_pattern = r'```python\n(.*?)\n```'
    code_matches = re.findall(code_pattern, reply, re.DOTALL)
    for code in code_matches:
        try:
            execution_result = tools.python(code)
            output_lines = [line.strip() for line in execution_result.strip().split('\n') if line.strip()]
            if output_lines:
                return output_lines[-1]
        except Exception:
            pass
    
    # Priority 3: Last line fallback
    lines = [line for line in reply.strip().splitlines() if line.strip()]
    return lines[-1] if lines else ""
