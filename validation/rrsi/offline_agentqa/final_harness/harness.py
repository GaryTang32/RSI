"""Evolved harness."""
import re


def solve(question, llm, tools, files):
    system = files.get("prompts/system.md", "")
    prompt = files["prompts/task.md"].replace("{question}", question)
    # [rrsi:tool] compute with the Python tool, then report its output
    reply = llm(prompt + "\n\nWrite python code that prints the final answer, in a ```python block.", system=system)
    blocks = re.findall(r"```python\n(.*?)```", reply, re.S)
    if blocks:
        out = tools.python(blocks[-1]).strip()
        if out:
            reply = llm("Question: " + question + "\nTool output: " + out.splitlines()[0] + "\nReply with a final line ANSWER: <value>.", system=system)
    lines = [line for line in reply.strip().splitlines() if line.strip()]
    return lines[-1] if lines else ""
