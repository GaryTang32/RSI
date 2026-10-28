"""
code_required harness: Mandatory code generation for state-tracking problems.

Mechanism (Axis F): Detect state-tracking problems and force code execution.
- Phase 1: Detect if problem requires state tracking (shuffled objects, swaps, etc.)
- Phase 2: Ask model to solve ONLY in Python code
- Phase 3: Execute the code and extract the answer from print output
- For non-tracking problems: fall back to seed behavior
"""

import re
import ast


def solve(question, llm, tools, files):
    system = files.get("prompts/system.md", "")

    # Detect if this is a state-tracking problem
    if is_tracking_problem(question):
        # Phase 1: Ask for pure Python solution
        code_request = create_code_request(question)
        code_response = llm(code_request, system=system)

        # Phase 2: Extract code blocks
        code_blocks = extract_code_blocks(code_response)

        if code_blocks:
            # Phase 3: Execute and collect results
            for code in code_blocks:
                try:
                    output = tools.python(code)
                    # Extract answer from output
                    answer = extract_answer_from_output(output)
                    if answer:
                        return f"ANSWER: {answer}"
                except Exception as e:
                    # Fall through to refinement
                    pass

            # If code didn't produce clear answer, ask model to extract it
            if output:
                refinement_prompt = f"""The code produced this output:

{output}

Based on this output, what is the final answer? Provide in format:
ANSWER: <answer>"""

                refinement_response = llm(refinement_prompt, system=system)
                return refinement_response.strip()

        # If no valid code extracted, fall back to seed
        prompt = files["prompts/task.md"].replace("{question}", question)
        reply = llm(prompt, system=system)
        return reply.strip()

    else:
        # Non-tracking problem: use seed behavior
        prompt = files["prompts/task.md"].replace("{question}", question)
        reply = llm(prompt, system=system)
        return reply.strip()


def is_tracking_problem(question):
    """Detect if question involves tracking object state through transformations."""
    tracking_patterns = [
        # Object position tracking
        r'\b(swap|exchange|switch|trade)\b',  # swap operations
        r'\b(partner|dancers?|holding|gift|book|present|item)\b',  # object references
        r'\bthrough\b.*\b(step|swap|exchange|action)\b',  # tracking through actions
        r'\bFirst,|Then,.*\b(swap|switch|exchange)\b',  # sequence of transformations
    ]

    question_lower = question.lower()

    # Count how many tracking patterns match
    matches = sum(1 for pattern in tracking_patterns if re.search(pattern, question_lower))

    # If multiple patterns or explicit tracking language, it's a tracking problem
    return matches >= 2


def create_code_request(question):
    """Create a prompt that requests Python code solution."""
    request = f"""IMPORTANT: Solve this problem ONLY by writing Python code that:
1. Initializes the starting state
2. Processes all transformations/swaps in order
3. Prints the final answer clearly

Do NOT explain in words. Write only a complete Python program that solves the problem and prints the answer.

Problem:
{question}

Write the Python code in a single code block:
```python
# Your complete solution here
```"""

    return request


def extract_code_blocks(text):
    """Extract Python code blocks from markdown text."""
    # Look for code blocks with ```python or just ```
    pattern = r'```(?:python)?\s*\n(.*?)\n```'
    matches = re.findall(pattern, text, re.DOTALL)
    return matches


def extract_answer_from_output(output):
    """Extract answer from code output."""
    # Look for lines that look like answers
    lines = output.strip().split('\n')

    # Look for explicit ANSWER: format
    for line in lines:
        if 'ANSWER:' in line.upper():
            return line.split('ANSWER:', 1)[1].strip()

    # Last non-empty line with content
    for line in reversed(lines):
        line = line.strip()
        if line and not line.startswith('>>>'):
            return line

    return None
