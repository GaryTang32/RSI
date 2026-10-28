"""
Candidate: Selective Code Execution for Tracking Problems

Mechanism: Conservative problem detection + code generation only for state tracking.
1. Detect ONLY state-tracking problems: explicit initial state + swap/exchange operations
2. For tracked problems: generate Python to track state changes
3. For all other problems: use baseline text reasoning
4. Fallback to text if code execution fails

Hypothesis: cand_code_solver failed because it applied code to logic puzzles and
boolean expressions where it was inappropriate (29.2% vs baseline 62.5%). By being
much more selective—only for tracking problems with explicit structure—code execution
helps shuffled_objects while preserving performance on logic/boolean problems.

Axis: C (selection/retrieval algorithm) - more conservative problem detection
Axis: F (model usage) - code generation for selected problem types
"""

import re


def solve(question, llm, tools, files):
    """Solve using selective code for tracking problems, text for others."""

    system = files.get("prompts/system.md", "")

    # Detect if this is a state-tracking problem
    if is_tracking_problem(question):
        # Try code-based solution
        code_answer = solve_with_code(question, llm, tools, files)
        if code_answer:
            return code_answer

    # Fall back to text reasoning (like seed)
    return solve_with_text(question, llm, files)


def is_tracking_problem(question):
    """
    Detect ONLY state-tracking problems with explicit structure.
    Much more conservative than cand_code_solver.

    Signals required (ALL must be present):
    1. Explicit initial state description
    2. Deterministic swap/exchange operations
    3. Domain-specific context (dance, gift, object)
    """
    lower_q = question.lower()

    # Initial state indicators (strong signals)
    initial_state_keywords = [
        'at the start',
        'initially',
        'at the beginning',
        'are each holding',
        'are each given',
        'is dancing with',
        'have a partner',
        'has a',
    ]

    # Operation keywords (indicating sequences of deterministic operations)
    operation_keywords = [
        'swap',
        'switch',
        'exchange',
        'trade',
        'traded',
        'switching',
        'swapped',
    ]

    # Domain context (gift exchange, dancing, shuffling)
    domain_keywords = [
        'dance',
        'partner',
        'gift',
        'present',
        'holding',
        'elastic',
        'swap',
    ]

    has_initial = any(kw in lower_q for kw in initial_state_keywords)
    has_operations = any(kw in lower_q for kw in operation_keywords)
    has_domain = any(kw in lower_q for kw in domain_keywords)

    # Only return True if ALL signals are present
    return has_initial and has_operations and has_domain


def solve_with_code(question, llm, tools, files):
    """Try to solve using code generation."""
    system = files.get("prompts/system.md", "")
    code_prompt = files["prompts/code_task.md"].replace("{question}", question)

    # Ask model to generate solving code
    reply = llm(code_prompt, system=system)

    # Extract Python code from response
    code = extract_code_block(reply)
    if not code:
        return None

    # Execute code
    try:
        output = tools.python(code)
        answer = extract_code_answer(output)
        if answer:
            return answer
    except Exception:
        pass

    return None


def solve_with_text(question, llm, files):
    """Solve using text reasoning (like seed)."""
    system = files.get("prompts/system.md", "")
    text_prompt = files["prompts/task.md"].replace("{question}", question)
    reply = llm(text_prompt, system=system)
    return reply.strip()


def extract_code_block(text):
    """Extract Python code from model response."""
    # Look for ```python ... ``` blocks
    match = re.search(r'```python\n(.*?)\n```', text, re.DOTALL)
    if match:
        return match.group(1)

    # Also try looking for just ``` ... ```
    match = re.search(r'```\n(.*?)\n```', text, re.DOTALL)
    if match:
        return match.group(1)

    return None


def extract_code_answer(output):
    """Extract ANSWER line from code execution output."""
    lines = output.split('\n')
    for line in lines:
        if line.startswith('ANSWER:'):
            return line[7:].strip()

    # Fallback: last non-empty line
    for line in reversed(lines):
        if line.strip():
            return line.strip()

    return None
