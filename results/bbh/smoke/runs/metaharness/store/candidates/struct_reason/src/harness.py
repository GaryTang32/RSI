"""Structured reasoning harness: explicit step scaffolding for general problem-solving.

Uses a prompt architecture that guides the model through: problem analysis →
step-by-step solving → verification → final answer. This approach is designed
to generalize across different reasoning task types (arithmetic, multiple choice,
yes/no, counting, sequences) by emphasizing systematic problem-solving rather
than task-specific shortcuts.
"""


def solve(question, llm, tools, files):
    system = files.get("prompts/system.md", "")
    prompt = files["prompts/task.md"].replace("{question}", question)
    reply = llm(prompt, system=system)
    return reply.strip()
