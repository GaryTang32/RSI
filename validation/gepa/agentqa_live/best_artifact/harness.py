"""Two-module harness: solver call -> optional Python tool run -> reporter call."""
import re


def solve(question, llm, tools, files):
    reply = llm("Question: " + question, system=files.get("prompts/solver.md", ""))
    code = re.findall(r"```python\n(.*?)```", reply, re.S)
    if code:
        out = tools.python(code[-1]).strip()
        draft = "Tool output: " + (out.splitlines()[-1] if out else "")
    else:
        lines = [l for l in reply.strip().splitlines() if l.strip()]
        draft = "Draft: " + (lines[-1] if lines else "")
    final = llm("Question: " + question + "\n" + draft, system=files.get("prompts/reporter.md", ""))
    lines = [l for l in final.strip().splitlines() if l.strip()]
    return final if "ANSWER" in final.upper() else (lines[-1] if lines else "")
