"""
Multi-turn guided harness: calculate with tool enforcement, then verify and format.
Axis: A (prompt), F (model-written verification).
"""


def solve(question, llm, tools, files):
    system = files.get("prompts/system.md", "")
    
    # Turn 1: Calculate with explicit tool usage
    calc_prompt = files["prompts/calculate.md"].replace("{question}", question)
    calc_reply = llm(calc_prompt, system=system)
    
    # Turn 2: Verify and format answer
    verify_prompt = files["prompts/verify.md"].replace(
        "{question}", question
    ).replace(
        "{calculation}", calc_reply
    )
    verify_reply = llm(verify_prompt, system=system)
    
    # Extract final answer: prioritize ANSWER: line
    for line in verify_reply.strip().splitlines():
        line_upper = line.upper()
        if "ANSWER:" in line_upper:
            return line.strip()
    
    # Fallback: last non-empty line
    lines = [line for line in verify_reply.strip().splitlines() if line.strip()]
    return lines[-1] if lines else ""
