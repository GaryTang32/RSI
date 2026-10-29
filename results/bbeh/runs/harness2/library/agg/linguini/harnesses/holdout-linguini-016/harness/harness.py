"""Seed harness: one direct model call; extract and return the final answer value."""


def solve(question, llm, tools, files):
    system = files.get("prompts/system.md", "")
    prompt = files["prompts/task.md"].replace("{question}", question)
    reply = llm(prompt, system=system)
    
    # Extract the answer from the line starting with ANSWER:
    for line in reply.split('\n'):
        if line.startswith('ANSWER:'):
            return line[len('ANSWER:'):].strip()
    
    # Fallback: return the stripped reply if no ANSWER line found
    return reply.strip()
