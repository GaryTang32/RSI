"""
Candidate: Code-Solver Harness

Mechanism: Structured problem detection + code generation + execution.
1. Detect if problem is "structured" (tracking, counting, boolean, math)
2. For structured problems: ask model to generate Python code to solve
3. Execute code via tools.python, extract answer from output
4. For unstructured problems or if code fails: fall back to text reasoning

Hypothesis: Many reasoning problems (tracking objects, evaluating boolean
expressions, counting patterns) are easier and more reliable to solve via
explicit code than text reasoning. Code-first approach improves accuracy
on these problem types while reducing errors from manual calculation.

Axis: C (retrieval/selection) - detect problem type and select code vs text
Axis: F (model usage inside harness) - use model to generate solving code
"""

import re


def solve(question, llm, tools, files):
    """Solve using code generation for structured problems, text for others."""

    system = files.get("prompts/system.md", "")

    # Detect if this is a structured problem
    if is_structured_problem(question):
        # Try code-based solution
        code_answer = solve_with_code(question, llm, tools, files)
        if code_answer:
            return code_answer

    # Fall back to text reasoning
    return solve_with_text(question, llm, files)


def is_structured_problem(question):
    """Detect if problem can be solved with code."""
    lower_q = question.lower()

    # Indicators of structured problems
    tracking_keywords = ['dance', 'switch', 'partner', 'trading', 'swapped', 'moved', 'position']
    counting_keywords = ['count', 'how many', 'number of', 'sum', 'total']
    boolean_keywords = ['true', 'false', 'not', 'and', 'or', 'expression', 'evaluate']
    pattern_keywords = ['pattern', 'rule', 'sequence', 'formula', 'equation']

    keywords = tracking_keywords + counting_keywords + boolean_keywords + pattern_keywords

    # Count matching keywords
    matches = sum(1 for kw in keywords if kw in lower_q)

    # Also check for indicators like: numbers in context, max/min operations
    has_numbers = any(c.isdigit() for c in question)
    has_operators = any(op in question for op in ['max(', 'min(', '<=', '>=', '=='])

    return matches >= 1 or (has_numbers and has_operators)


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
