"""Enhanced reasoning harness: Single-call improvement via system prompt guidance.

Mechanism (Axis A): Improve the SYSTEM prompt to encourage methodical step-by-step reasoning.
- Same single LLM call as seed
- Improved system prompt emphasizes thinking through problems carefully
- Especially helps with complex problems: shuffled objects, linguistic patterns, logic
- No detection logic, so won't break on any problem type
"""


def solve(question, llm, tools, files):
    system = files.get("prompts/system.md", "")
    prompt = files["prompts/task.md"].replace("{question}", question)
    reply = llm(prompt, system=system)
    return reply.strip()
