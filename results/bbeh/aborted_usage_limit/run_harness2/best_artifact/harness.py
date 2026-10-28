"""Seed harness: one direct model call; the reply (ending in an ANSWER line) is the deliverable."""


def solve(question, llm, tools, files):
    system = files.get("prompts/system.md", "")
    prompt = files["prompts/task.md"].replace("{question}", question)
    reply = llm(prompt, system=system)
    return reply.strip()
