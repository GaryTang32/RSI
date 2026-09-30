"""Harness that enforces Python computation and structured answer output."""


def solve(question, llm, tools, files):
    system = """You are an expert at solving computational problems.

For any computational task (calculating numbers, sums, modulos, digit sums, bit counts, etc.), you MUST:
1. Write Python code to perform the exact computation
2. Show the code and/or its output
3. State the final answer on a line that begins with: PYTHON_RESULT: <value>

Always prioritize exact computation over narrative explanation. Use Python for verification."""
    
    prompt = files["prompts/task.md"].replace("{question}", question)
    reply = llm(prompt, system=system)
    
    # Try to extract from PYTHON_RESULT marker
    for line in reply.split('\n'):
        line = line.strip()
        if line.startswith('PYTHON_RESULT:'):
            return line.replace('PYTHON_RESULT:', '').strip()
    
    # Fallback: return last non-empty line if no marker found
    lines = [line.strip() for line in reply.split('\n') if line.strip()]
    return lines[-1] if lines else ""
