"""Harness with structured verification and clear answer format."""


def solve(question, llm, tools, files):
    system = files.get("prompts/system.md", "")
    prompt = files["prompts/task.md"].replace("{question}", question)
    reply = llm(prompt, system=system)
    
    # Extract ANSWER: line if present (preferred format)
    lines = reply.strip().splitlines()
    for line in lines:
        if "ANSWER:" in line:
            parts = line.split("ANSWER:", 1)
            if len(parts) == 2:
                value = parts[1].strip()
                # Take only the first token (the numeric value)
                value = value.split()[0] if value else ""
                return value
    
    # Fallback: return last non-empty line
    lines = [line for line in lines if line.strip()]
    return lines[-1] if lines else ""
