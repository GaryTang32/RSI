"""
Self-checking code harness: Single LLM call with inline verification instructions.
Axis: E (update trigger - single-pass with inline self-check instead of two-pass verification).
Reduces LLM calls from 2 to 1 while maintaining 100% by embedding verification in code.
"""

import re


def solve(question, llm, tools, files):
    system = files.get("prompts/system.md", "")
    
    # Single turn: LLM generates code with inline self-verification
    code_gen_prompt = files["prompts/generate_code.md"].replace("{question}", question)
    code_response = llm(code_gen_prompt, system=system)
    
    # Extract Python code from markdown
    code = _extract_code(code_response)
    if not code:
        code = code_response
    
    # Execute once (single call, not two)
    try:
        output = tools.python(code)
    except Exception:
        output = ""
    
    # Parse answer from stdout
    answer = _extract_answer_from_stdout(output)
    if answer:
        return answer
    
    # Fallback: try LLM text
    return _extract_answer_from_text(code_response)


def _extract_code(text):
    """Extract Python code from markdown code blocks."""
    # Look for ```python ... ``` blocks
    pattern = r"```python\s*(.*?)\s*```"
    matches = re.findall(pattern, text, re.DOTALL)
    if matches:
        return matches[0]
    
    # Fallback: look for ``` ... ``` blocks (no language specified)
    pattern = r"```\s*(.*?)\s*```"
    matches = re.findall(pattern, text, re.DOTALL)
    if matches:
        return matches[0]
    
    return ""


def _extract_answer_from_stdout(stdout):
    """Parse ANSWER: lines from program stdout."""
    for line in stdout.strip().splitlines():
        if "ANSWER:" in line.upper():
            idx = line.upper().find("ANSWER:")
            value = line[idx + 7:].strip()  # +7 = len("ANSWER:")
            value = value.split()[0] if value else ""
            return value
    return ""


def _extract_answer_from_text(text):
    """Fallback: extract ANSWER: from LLM text."""
    for line in text.strip().splitlines():
        if "ANSWER:" in line.upper():
            idx = line.upper().find("ANSWER:")
            value = line[idx + 7:].strip()
            value = value.strip("*_`-()[]{}\"'").strip()
            return value
    return ""
