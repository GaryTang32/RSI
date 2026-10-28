"""
Candidate: Error Recovery Harness

Mechanism: Two-attempt approach with recovery.
1. Initial attempt with minimal prompt (like seed)
2. If ANSWER line not found or output seems malformed, trigger recovery:
   - Ask model to explicitly format final answer
   - Extract final line as answer

Hypothesis: Many failed answers are due to formatting/extraction issues, not
reasoning failures. A recovery mechanism that enforces format when needed
improves answer extraction without significant cost increase.

Axis: E (learning/update trigger) - trigger recovery based on extraction success
"""

import re


def solve(question, llm, tools, files):
    """Solve using initial attempt + error recovery if needed."""

    system = files.get("prompts/system.md", "")
    prompt = files["prompts/task.md"].replace("{question}", question)

    # First attempt
    reply = llm(prompt, system=system)
    answer = extract_answer(reply)

    # If extraction succeeded, return
    if answer:
        return answer

    # Recovery: ask for explicit format
    recovery_prompt = files["prompts/recovery.md"].replace("{question}", question).replace("{previous_attempt}", reply)
    recovery_reply = llm(recovery_prompt, system=system)
    answer = extract_answer(recovery_reply)

    if answer:
        return answer

    # Final fallback: use last non-empty line
    lines = recovery_reply.strip().split('\n')
    for line in reversed(lines):
        if line.strip():
            return line.strip()

    return recovery_reply.strip()


def extract_answer(text):
    """Extract ANSWER line or None if not found."""
    lines = text.split('\n')
    for line in lines:
        if line.startswith('ANSWER:'):
            # Extract content after ANSWER:
            answer = line[7:].strip()
            return answer
    return None
