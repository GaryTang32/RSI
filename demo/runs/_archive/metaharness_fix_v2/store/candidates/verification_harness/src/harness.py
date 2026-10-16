def solve(question, llm, tools, files):
    """
    Verification harness: Two-pass approach with independent verification.
    First pass computes the answer; second pass asks the model to independently
    verify it, which can catch arithmetic errors through recalculation.
    """
    system = files.get("prompts/system.md", "")
    prompt_template = files["prompts/task.md"]
    prompt = prompt_template.replace("{question}", question)
    
    # ===== PASS 1: Initial computation =====
    first_prompt = f"""{prompt}

Solve this problem step by step, showing all your work. When you have your final answer, output it on a line in this exact format:

ANSWER: <value>

Do not add any text before or after the answer on that line."""
    
    reply1 = llm(first_prompt, system=system)
    
    # Extract tentative answer
    tentative_answer = None
    for line in reply1.strip().splitlines():
        line_stripped = line.strip()
        if line_stripped.startswith("ANSWER:"):
            tentative_answer = line_stripped[len("ANSWER:"):].strip()
            break
    
    if not tentative_answer:
        lines = [l for l in reply1.strip().splitlines() if l.strip()]
        tentative_answer = lines[-1] if lines else ""
    
    # ===== PASS 2: Independent verification =====
    verify_prompt = f"""{prompt}

I calculated the answer to be: {tentative_answer}

Please solve this problem independently and verify the correctness of this answer. Re-calculate from scratch without relying on my answer. If my answer is correct, confirm it. If it is wrong, provide the correct answer.

Output your final verified answer on a line in this exact format:

ANSWER: <value>

Do not add any text before or after the answer on that line."""
    
    reply2 = llm(verify_prompt, system=system)
    
    # Extract verified answer
    for line in reply2.strip().splitlines():
        line_stripped = line.strip()
        if line_stripped.startswith("ANSWER:"):
            verified_answer = line_stripped[len("ANSWER:"):].strip()
            if verified_answer:
                return verified_answer
    
    # Fallback: last non-empty line from verification response
    lines = [l for l in reply2.strip().splitlines() if l.strip()]
    if lines:
        return lines[-1]
    
    # Last resort: return tentative if verification yielded nothing
    return tentative_answer
