"""Enhanced harness: answer format cleaning and output simplification for robust extraction."""

import re


def solve(question, llm, tools, files):
    system = files.get("prompts/system.md", "")
    prompt = files["prompts/task.md"].replace("{question}", question)
    reply = llm(prompt, system=system)
    
    # Clean choice-format answers (remove descriptive text after choice letters)
    cleaned_reply = _clean_answer_format(reply)
    
    # Simplify output: keep only answer context, discard verbose preamble
    simplified_reply = _simplify_output(cleaned_reply)
    
    return simplified_reply.strip()


def _clean_answer_format(text):
    """Remove descriptive text from choice-format answers in ANSWER lines."""
    result = []
    for line in text.split('\n'):
        if 'ANSWER:' in line.upper():
            # Find and clean the answer part
            match = re.search(r'ANSWER:\s*(.+?)$', line, re.IGNORECASE)
            if match:
                answer = match.group(1).strip()
                cleaned_answer = _clean_choice_answer(answer)
                # Reconstruct line with cleaned answer
                line = line[:match.start()] + 'ANSWER: ' + cleaned_answer
            result.append(line)
        else:
            result.append(line)
    return '\n'.join(result)


def _clean_choice_answer(answer):
    """Clean a choice answer, removing extraneous descriptive text.
    
    Handles patterns like:
    - 'C' -> '(C)'  (bare letter to parenthesized)
    - '(D) Rodrigo' -> '(D)'  (choice with description)
    - '(B)' -> '(B)'  (already clean)
    - 'some answer' -> 'some answer'  (non-choice, unchanged)
    
    Does NOT match bare letter followed by text (e.g., 'B 5') to avoid
    corrupting counting/tracking task answers like variable assignments.
    """
    # Case 1: Bare choice letter (A-F) alone -> wrap in parentheses
    m = re.match(r'^([A-F])(\s*)$', answer)
    if m:
        return f"({m.group(1)})"
    
    # Case 2: Choice in parentheses with description: (A)-(F) + space + text -> keep just letter
    m = re.match(r'^\(([A-F])\)\s+\S.+$', answer)
    if m:
        return f"({m.group(1)})"
    
    # Case 3: Choice in parentheses alone: (A)-(F) optionally with trailing space -> keep as-is
    m = re.match(r'^\(([A-F])\)(\s*)$', answer)
    if m:
        return answer
    
    # Default: return as-is (non-choice answer, multi-word answer, etc.)
    return answer


def _simplify_output(text):
    """Keep just the relevant answer context, discard excessive preamble explanation.
    
    Finds the ANSWER: line and returns from a few lines before it to the end,
    removing long preamble while preserving immediate context.
    """
    lines = text.split('\n')
    
    # Find ANSWER line
    answer_line_idx = -1
    for i, line in enumerate(lines):
        if 'ANSWER:' in line.upper():
            answer_line_idx = i
            break
    
    if answer_line_idx == -1:
        # No ANSWER line found, return full text
        return text
    
    # Keep from 1 line before ANSWER to the end (preserves immediate context)
    start_idx = max(0, answer_line_idx - 1)
    return '\n'.join(lines[start_idx:])
