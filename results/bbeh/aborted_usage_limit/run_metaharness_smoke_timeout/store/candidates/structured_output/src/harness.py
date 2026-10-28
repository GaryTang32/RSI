"""Structured output harness: Single-call improvement via explicit reasoning space.

Mechanism (Axis D): Modify task prompt to request explicit working/reasoning section
before the final answer.
- Same single LLM call as seed
- Task prompt explicitly asks for reasoning first, then ANSWER line
- Provides mental space for tracking state and working through logic
- Helps especially with shuffled_objects and linguini patterns
- No detection logic, works on all problem types
"""


def solve(question, llm, tools, files):
    system = files.get("prompts/system.md", "")
    prompt = files["prompts/task.md"].replace("{question}", question)
    reply = llm(prompt, system=system)
    return reply.strip()
