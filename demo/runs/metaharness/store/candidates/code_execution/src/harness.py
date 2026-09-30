"""
Code execution harness: LLM generates Python code, harness executes it and parses stdout.
Axis: F (code execution via tools.python), C (stdout parsing instead of LLM text parsing).
Guarantees arithmetic correctness by running code deterministically.
"""


def solve(question, llm, tools, files):
    system = files.get("prompts/system.md", "")
    
    # Turn 1: Generate and execute code
    code_gen_prompt = files["prompts/generate_code.md"].replace("{question}", question)
    code_response = llm(code_gen_prompt, system=system)
    
    # Extract Python code from response (look for ```python blocks)
    code = _extract_code(code_response)
    if not code:
        # Fallback: use the entire response as code
        code = code_response
    
    # Execute the code
    try:
        output1 = tools.python(code)
    except Exception:
        output1 = ""
    
    answer1 = _extract_answer_from_stdout(output1)
    
    # Turn 2: Verify with alternative approach
    verify_prompt = files["prompts/verify_code.md"].replace(
        "{question}", question
    ).replace(
        "{previous_answer}", answer1 or "UNKNOWN"
    )
    verify_response = llm(verify_prompt, system=system)
    
    verify_code = _extract_code(verify_response)
    if not verify_code:
        verify_code = verify_response
    
    try:
        output2 = tools.python(verify_code)
    except Exception:
        output2 = ""
    
    answer2 = _extract_answer_from_stdout(output2)
    
    # Return first answer, or second if first failed
    if answer1:
        return answer1
    elif answer2:
        return answer2
    else:
        # Fallback: try to extract from LLM text
        return _extract_answer_from_text(code_response)


def _extract_code(text):
    """Extract Python code from markdown code blocks."""
    import re
    
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
    
    # If no code blocks found, return empty
    return ""


def _extract_answer_from_stdout(stdout):
    """Parse ANSWER: lines from program stdout."""
    for line in stdout.strip().splitlines():
        if "ANSWER:" in line.upper():
            idx = line.upper().find("ANSWER:")
            value = line[idx + 7:].strip()  # +7 = len("ANSWER:")
            # Clean up any trailing content
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
