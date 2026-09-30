"""
Clean extraction harness: multi-method with robust ANSWER: parsing and markdown cleanup.
Axis: F (extraction), A (format clarity).
Base: multi_method, but fixes the extraction bottleneck.
"""


def solve(question, llm, tools, files):
    system = files.get("prompts/system.md", "")
    
    # Method A: Direct calculation
    method_a_prompt = files["prompts/method.md"].replace(
        "{question}", question
    ).replace(
        "{method_label}", "Method A: Direct Calculation"
    )
    result_a = llm(method_a_prompt, system=system)
    
    # Method B: Alternative/verification approach
    method_b_prompt = files["prompts/method.md"].replace(
        "{question}", question
    ).replace(
        "{method_label}", "Method B: Alternative Approach"
    )
    result_b = llm(method_b_prompt, system=system)
    
    # Compare and select
    compare_prompt = files["prompts/compare.md"].replace(
        "{question}", question
    ).replace(
        "{method_a_result}", result_a
    ).replace(
        "{method_b_result}", result_b
    )
    final_reply = llm(compare_prompt, system=system)
    
    # Robust extraction: find ANSWER: line and clean markdown
    answer = _extract_answer(final_reply)
    if answer:
        return answer
    
    # Fallback: last non-empty line with cleanup
    lines = [line.strip() for line in final_reply.strip().splitlines() if line.strip()]
    if lines:
        return _clean_answer(lines[-1])
    
    return ""


def _extract_answer(text):
    """Find ANSWER: line and extract clean value."""
    for line in text.strip().splitlines():
        line_upper = line.upper()
        if "ANSWER:" in line_upper:
            # Find the position of ANSWER: and extract everything after it
            idx = line_upper.find("ANSWER:")
            value = line[idx + 7:]  # +7 = len("ANSWER:")
            return _clean_answer(value)
    return ""


def _clean_answer(text):
    """Strip markdown, formatting, and extra whitespace from answer."""
    text = text.strip()
    
    # Remove leading/trailing markdown
    text = text.strip("*_`-()[]{}\"'")
    
    # Remove markdown formatting patterns
    text = text.replace("**", "")
    text = text.replace("__", "")
    text = text.replace("`", "")
    
    # Strip again after cleanup
    text = text.strip()
    
    return text if text else ""
