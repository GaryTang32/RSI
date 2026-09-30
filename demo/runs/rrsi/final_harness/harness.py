"""Harness that executes tool invocations and feeds results back to LLM."""

import re


def solve(question, llm, tools, files):
    """Solve a question by invoking tools and grounding answers in their output."""
    system = files.get("prompts/system.md", "")
    prompt = files["prompts/task.md"].replace("{question}", question)
    
    # First LLM call: generate solution approach and code
    initial_response = llm(prompt, system=system)
    
    # Parse and execute any tool invocations from the response
    tool_output = parse_and_execute_tools(initial_response, tools)
    
    # Only make followup call if tools actually executed and produced output
    if tool_output:
        followup_prompt = (
            f"Based on the following computation results:\n\n"
            f"{tool_output}\n\n"
            f"Question: {question}\n\n"
            f"Provide your final answer in exactly this format: ANSWER: <value>\n"
            f"Do not attempt manual verification or recalculation; report only the computed result."
        )
        final_response = llm(followup_prompt, system=system)
    else:
        final_response = initial_response
    
    # Extract answer: first try ANSWER: format, then fall back to last line
    answer = extract_answer(final_response)
    return answer


def extract_answer(text):
    """
    Extract final answer from response text.
    First tries to find 'ANSWER: <value>' pattern, then falls back to last line.
    """
    # Pattern 1: Look for ANSWER: line (case-insensitive, at start of line)
    answer_pattern = r'^ANSWER:\s*(.+)$'
    for line in text.splitlines():
        match = re.search(answer_pattern, line, re.IGNORECASE)
        if match:
            return line.strip()  # Return full ANSWER: line
    
    # Pattern 2: Fall back to last non-empty line
    lines = [line for line in text.strip().splitlines() if line.strip()]
    return lines[-1] if lines else ""


def parse_and_execute_tools(text, tools):
    """
    Parse and execute tool invocations from LLM response.
    Supports both <invoke> XML blocks and markdown code blocks.
    Returns concatenated tool outputs, or empty string if none found/executed.
    """
    outputs = []
    
    # Pattern 1: <invoke name="bash"><parameter name="command">COMMAND</parameter></invoke>
    invoke_pattern = r'<invoke name="bash">\s*<parameter name="command">(.*?)</parameter>\s*</invoke>'
    
    for match in re.finditer(invoke_pattern, text, re.DOTALL):
        cmd = match.group(1).strip()
        
        # Only process python3 commands
        if 'python3' not in cmd:
            continue
        
        code = extract_python_code_from_bash(cmd)
        if code:
            try:
                result = tools.python(code)
                if result and result.strip():
                    outputs.append(result)
            except Exception:
                # Skip execution errors to maintain robustness
                pass
    
    # Pattern 2: markdown code blocks with python
    # Flexible: allows optional whitespace around 'python' keyword and newline
    # Matches: ```python\n...\n``` or ``` python \n...``` etc.
    markdown_pattern = r'```\s*python\s*\n(.*?)```'
    
    for match in re.finditer(markdown_pattern, text, re.DOTALL):
        code = match.group(1).strip()
        if code:
            try:
                result = tools.python(code)
                if result and result.strip():
                    outputs.append(result)
            except Exception:
                # Skip execution errors to maintain robustness
                pass
    
    return "\n".join(outputs)


def extract_python_code_from_bash(cmd):
    """Extract Python code from a bash command string."""
    # Case 1: python3 -c "..." or python3 -c '...'
    if ' -c ' in cmd:
        match = re.search(r'-c\s+["\']([^"\']*)["\']', cmd)
        if match:
            return match.group(1)
    
    # Case 2: python3 << 'EOF' ... EOF or python3 << "EOF" ... EOF
    if '<<' in cmd:
        match = re.search(r'<<\s+["\']?EOF["\']?(.*?)EOF', cmd, re.DOTALL)
        if match:
            return match.group(1).strip()
    
    return None
