"""
Harness with semantic task-type detection (signal scoring) instead of keyword lists.
Routes to specialized logic or data strategies based on weighted semantic signals.
"""

import re


def detect_task_type_semantic(question):
    """
    Detect task type using semantic signal scoring.
    Scores multiple signals (financial terms, dates, logic keywords, constraints)
    and returns the type with the higher score.
    """
    q_lower = question.lower()
    
    # Data-task indicators: financial, transactional, computational
    data_signals = {
        'transaction': 3, 'ledger': 3, 'amount': 2, 'paid': 2, 'refund': 2,
        'currency': 2, 'convert': 2, 'rate': 2, 'average': 2, 'sum': 2,
        'total': 2, 'balance': 1, 'calculate': 1, 'filter': 1, 'usd': 2,
        'eur': 2, 'export': 1, 'accounting': 2, 'settlement': 2
    }
    
    # Logic-task indicators: constraint, deduction, reasoning
    logic_signals = {
        'logic puzzle': 4, 'clue': 3, 'constraint': 3, 'must be': 2,
        'person': 2, 'house': 2, 'color': 1, 'pet': 1, 'hobby': 1,
        'immediately left': 3, 'next to': 2, 'immediately right': 3,
        'position': 2, 'standing in': 2, 'sitting': 1, 'row': 1,
        'different': 1, 'who': 1, 'which': 1, 'determine': 1
    }
    
    # Calculate weighted scores
    data_score = sum(weight for word, weight in data_signals.items() if word in q_lower)
    logic_score = sum(weight for word, weight in logic_signals.items() if word in q_lower)
    
    # Additional modifiers
    if '$' in question or re.search(r'\d+,\d{2}', question):  # Currency amounts
        data_score += 2
    if re.search(r'\b(person|people|living|sitting)\b', q_lower):  # People constraints
        logic_score += 1
    
    # Return result
    return "data" if data_score > logic_score else "logic"


def solve(question, llm, tools, files):
    task_type = detect_task_type_semantic(question)
    
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
