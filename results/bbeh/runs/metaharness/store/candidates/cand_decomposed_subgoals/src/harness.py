"""
Candidate 2: Decomposed Subgoals Harness

Mechanism: Task-specific decomposition strategies with explicit sub-goal structure
- Different approaches for different problem types
- Explicit enumeration of sub-goals
- Structured output format with intermediate results
- Sequential verification of each sub-goal

Hypothesis: Breaking problems into explicit sub-goals and working through them
systematically improves accuracy on complex problems. Different problem types
benefit from different decomposition strategies.

Axis: A (prompt template) - but fundamentally different structure focused on
      explicit sub-goal enumeration and sequential verification
"""


def solve(question, llm, tools, files):
    system = files.get("prompts/system.md", "")
    prompt = files["prompts/task.md"].replace("{question}", question)
    reply = llm(prompt, system=system)
    return reply.strip()
