"""
Multi-method harness: solve via two independent approaches, then compare and decide.
Axis: C (selection via comparison), B (state: intermediate results), F (model usage: cross-verification).
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
    
    # Extract final answer: prioritize ANSWER: line
    for line in final_reply.strip().splitlines():
        line_upper = line.upper()
        if "ANSWER:" in line_upper:
            return line.strip()
    
    # Fallback: last non-empty line
    lines = [line for line in final_reply.strip().splitlines() if line.strip()]
    return lines[-1] if lines else ""
