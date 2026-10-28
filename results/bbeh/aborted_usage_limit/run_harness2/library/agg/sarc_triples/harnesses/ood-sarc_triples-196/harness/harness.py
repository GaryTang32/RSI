"""Seed harness: one direct model call; the reply's ANSWER line is the deliverable."""


def solve(question, llm, tools, files):
    system = files.get("prompts/system.md", "")
    prompt = files["prompts/task.md"].replace("{question}", question)
    reply = llm(prompt, system=system)
    
    # Extract only the ANSWER line to match requested format (comma-separated labels)
    for line in reply.strip().split('\n'):
        if line.startswith('ANSWER:'):
            return line.replace('ANSWER:', '').strip()
    
    # Fallback if ANSWER line not found
    return reply.strip()
