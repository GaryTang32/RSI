"""Scripted edit pools for artifacts that are not autoresearch-native.

:func:`harness_edit_pool` targets the AgentQA-style harness layout
(``harness.py`` with ``solve(question, llm, tools, files)``, ``prompts/system.md``,
``prompts/task.md``) so the offline :class:`~rsi.autoresearch.MockResearchAgent`
can drive the loop on :mod:`rsi.domains.agentqa` through
:class:`~rsi.autoresearch.DomainResearchTask`. The edits are generic harness
ideas (reason step by step, state the answer format, verify, use the Python
tool), one neutral rewording and one trivially broken edit with its fix.
"""
from __future__ import annotations

from .agent import ScriptedEdit, text_edit

TOOL_HARNESS = '''"""Harness: ask the model for Python code, run it with the tool, report the printed value."""


def solve(question, llm, tools, files):
    system = files.get("prompts/system.md", "")
    prompt = files["prompts/task.md"].replace("{question}", question)
    reply = llm(prompt + "\\n\\nWrite python code that prints the answer, in a ```python block.", system=system)
    if "```python" in reply:
        code = reply.split("```python", 1)[1].split("```", 1)[0]
        out = tools.python(code).strip()
        if out:
            return "ANSWER: " + out.splitlines()[-1]
    lines = [line for line in reply.strip().splitlines() if line.strip()]
    return lines[-1] if lines else ""
'''


def _replace_file(name: str, kind: str, path: str, text: str, desc: str) -> ScriptedEdit:
    def apply(files):
        if path not in files or files[path] == text:
            return None
        return {path: text}, desc

    return ScriptedEdit(name, kind, apply, group=name)


def harness_edit_pool() -> list[ScriptedEdit]:
    return [
        text_edit("step_by_step", "helpful", "prompts/system.md", "", "\nThink step by step before answering.\n",
                  "system prompt: think step by step"),
        text_edit("answer_format", "helpful", "prompts/task.md", "", "\nEnd with a line 'ANSWER: <value>'.\n",
                  "task prompt: state the ANSWER: format"),
        text_edit("verify", "helpful", "prompts/system.md", "", "\nVerify and double-check your result.\n",
                  "system prompt: verify the result"),
        _replace_file("python_tool", "helpful", "harness.py", TOOL_HARNESS, "harness: compute answers with the Python tool"),
        text_edit("polite", "neutral", "prompts/system.md", "You are a helpful assistant.",
                  "You are a friendly and helpful assistant.", "system prompt: friendlier wording"),
        text_edit("broken_return", "crash", "harness.py", "    return lines[-1] if lines else \"\"\n",
                  "    return lines[-1] if lines else \"\"\n    retrun None\n", "harness: return cleanup (typo)",
                  fix=lambda f: {"harness.py": f["harness.py"].replace("    retrun None\n", "", 1)}),
    ]
