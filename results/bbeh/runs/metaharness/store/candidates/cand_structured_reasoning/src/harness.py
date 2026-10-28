"""
Candidate 1: Structured Reasoning Harness

Mechanism: Task-aware system prompt that forces explicit decomposition
- Identify problem type (tracking, pattern learning, reasoning, calculation)
- State reasoning strategy before execution
- Work through problem systematically
- Verify answer before finalizing

Hypothesis: Making the model's reasoning explicit and forcing verification
improves accuracy on complex problems without requiring multiple calls.

Axis: A (prompt template) + B (memory/state)
"""


def solve(question, llm, tools, files):
    system = files.get("prompts/system.md", "")
    prompt = files["prompts/task.md"].replace("{question}", question)
    reply = llm(prompt, system=system)
    return reply.strip()
