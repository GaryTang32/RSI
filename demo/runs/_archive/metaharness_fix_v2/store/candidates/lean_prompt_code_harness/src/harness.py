import re

def solve(question, llm, tools, files):
    """
    Lean prompt code harness: Simplified, minimal-overhead prompt without context extraction.
    Relies on direct model reasoning and code generation without augmented scaffolding.
    This reduces token usage while maintaining accuracy through clear, unified instructions.
    """
    system = files.get("prompts/system.md", "")
    prompt_template = files["prompts/task.md"]
    prompt = prompt_template.replace("{question}", question)
    
    # Minimal, unified prompt: no context extraction, no augmentation
    lean_prompt = f"""{prompt}

Solve this problem:

1. Think through the approach step by step.
2. Provide your answer either:
   A) As Python code in a ```python code block that outputs ONLY the final answer.
   B) As a single line: ANSWER: <value>

Be concise. Output only code or the answer line; no extra explanation."""
    
    reply = llm(lean_prompt, system=system)
    
    # Extract all Python code blocks
    code_blocks = re.findall(r'```(?:python)?\s*\n(.*?)\n```', reply, re.DOTALL)
    
    # Try code blocks in reverse order (prefer the last/final code block)
    if code_blocks:
        for code in reversed(code_blocks):
            try:
                output = tools.python(code)
                lines = [l.strip() for l in output.strip().splitlines() if l.strip()]
                if lines:
                    return lines[-1]
            except Exception:
                continue
    
    # Fallback 1: Look for ANSWER: marker
    for line in reply.strip().splitlines():
        line_stripped = line.strip()
        if line_stripped.startswith("ANSWER:"):
            answer = line_stripped[len("ANSWER:"):].strip()
            if answer:
                return answer
    
    # Fallback 2: Return last non-empty line
    lines = [l for l in reply.strip().splitlines() if l.strip()]
    return lines[-1] if lines else ""
