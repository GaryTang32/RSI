"""Seed harness: one direct model call; extract and return the ANSWER line as the deliverable."""


def solve(question, llm, tools, files):
    system = files.get("prompts/system.md", "")
    prompt = files["prompts/task.md"].replace("{question}", question)
    reply = llm(prompt, system=system)
    
    # Extract the ANSWER line from the LLM output and return only the answer portion
    for line in reply.split('\n'):
        if line.startswith('ANSWER:'):
            return line.replace('ANSWER:', '').strip()
    
    # Fallback if no ANSWER line found
    return reply.strip()
