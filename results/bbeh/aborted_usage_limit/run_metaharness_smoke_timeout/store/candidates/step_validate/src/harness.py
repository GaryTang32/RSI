"""
step_validate harness: Execute code blocks for validation.

Mechanism (Axis F): Use tools.python() to actually run code the model writes.
- Encourages models to write code for complex problems
- Allows validation of logic puzzles, object tracking, boolean expressions
- If model writes code but gives no ANSWER, ask it to run code and provide answer
"""

import re


def solve(question, llm, tools, files):
    system = files.get("prompts/system.md", "")
    prompt = files["prompts/task.md"].replace("{question}", question)

    # First call: Let model solve with code
    reply = llm(prompt, system=system)

    # Try to extract code blocks and run them
    code_blocks = extract_code_blocks(reply)

    if code_blocks:
        # Execute each code block
        execution_results = []
        for code in code_blocks:
            try:
                result = tools.python(code)
                execution_results.append(result)
            except Exception as e:
                execution_results.append(f"Error: {str(e)}")

        # Check if we already have an ANSWER line
        answer = extract_answer(reply)

        if not answer and execution_results:
            # Model wrote code but didn't extract final answer
            # Ask it to run the code and provide the answer
            execution_context = "\n".join(
                [f"Code block {i+1} output:\n{result}"
                 for i, result in enumerate(execution_results)]
            )

            refinement_prompt = f"""I ran the code you provided. Here are the results:

{execution_context}

Based on these results, please provide your final answer in the format:
ANSWER: <your answer>"""

            refinement_reply = llm(refinement_prompt, system=system)
            return refinement_reply.strip()

    return reply.strip()


def extract_code_blocks(text):
    """Extract Python code blocks from markdown text."""
    pattern = r"```(?:python)?\s*\n(.*?)\n```"
    matches = re.findall(pattern, text, re.DOTALL)
    return matches


def extract_answer(text):
    """Extract the ANSWER line from text."""
    # Look for ANSWER: pattern (case-insensitive)
    match = re.search(r"ANSWER:\s*(.+?)(?:\n|$)", text, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return None
