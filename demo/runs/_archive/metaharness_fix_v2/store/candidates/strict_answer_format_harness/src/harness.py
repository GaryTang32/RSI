def solve(question, llm, tools, files):
    """
    Strict format harness: Use an explicit output format ('ANSWER: <value>') in the prompt
    and extract the answer by looking for that marker. This encourages consistent model behavior
    and removes ambiguity in answer extraction, reducing errors on tasks where formatting varies
    or the model outputs multiple numbers.
    """
    system = files.get("prompts/system.md", "")
    prompt_template = files["prompts/task.md"]
    prompt = prompt_template.replace("{question}", question)
    
    # Request answer in explicit format
    strict_prompt = f"""{prompt}

Solve this problem step by step. When you have your final numerical answer, output it on a line in this exact format:

ANSWER: <value>

Do not add any text before or after the answer on that line."""
    
    reply = llm(strict_prompt, system=system)
    
    # Extract the ANSWER: line
    for line in reply.strip().splitlines():
        line_stripped = line.strip()
        if line_stripped.startswith("ANSWER:"):
            answer = line_stripped[len("ANSWER:"):].strip()
            if answer:
                return answer
    
    # Fallback: return last non-empty line
    lines = [l for l in reply.strip().splitlines() if l.strip()]
    return lines[-1] if lines else ""
