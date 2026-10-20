import re

def solve(question, llm, tools, files):
    """
    Context-extracted code harness: Pre-process the question to extract key
    quantities and operation types, feed them back as structured hints to improve
    code generation efficiency without sacrificing correctness.
    """
    system = files.get("prompts/system.md", "")
    prompt_template = files["prompts/task.md"]
    prompt = prompt_template.replace("{question}", question)
    
    # Extract key quantities and operations from the question
    numbers = re.findall(r'\b\d+\b', question)
    unique_numbers = list(dict.fromkeys(numbers))[:10]  # Limit to 10 unique numbers
    
    operations_dict = {
        r'factorial|!': 'factorial',
        r'mod|modulo': 'modular arithmetic',
        r'sum.*digit|digit.*sum': 'digit sum',
        r'binary|bit': 'binary representation',
        r'remainder': 'division remainder',
        r'power|\^|\*\*': 'exponentiation',
        r'lcm|gcd': 'number theory',
    }
    
    detected_ops = []
    for pattern, op_name in operations_dict.items():
        if re.search(pattern, question, re.IGNORECASE):
            detected_ops.append(op_name)
    
    # Build augmented prompt with context hints
    context_hints = ""
    if unique_numbers:
        context_hints += f"Key numbers in problem: {', '.join(unique_numbers[:5])}\n"
    if detected_ops:
        context_hints += f"Operations involved: {', '.join(detected_ops)}\n"
    
    augmented_prompt = f"""{prompt}

**Problem Context:**
{context_hints if context_hints else "Standard numerical computation problem."}

Please solve this problem:

1. Briefly explain the approach.
2. Provide Python code in a ```python code block that outputs ONLY the final numerical answer on a single line.

The Python code must be syntactically correct and compute the exact answer without intermediate output."""
    
    reply = llm(augmented_prompt, system=system)
    
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
    
    # Fallback 1: Look for ANSWER: marker in text response
    for line in reply.strip().splitlines():
        line_stripped = line.strip()
        if line_stripped.startswith("ANSWER:"):
            answer = line_stripped[len("ANSWER:"):].strip()
            if answer:
                return answer
    
    # Fallback 2: Return last non-empty line
    lines = [l for l in reply.strip().splitlines() if l.strip()]
    return lines[-1] if lines else ""
