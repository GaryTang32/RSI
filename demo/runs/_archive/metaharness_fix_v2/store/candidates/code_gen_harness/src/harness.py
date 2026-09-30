import re

def solve(question, llm, tools, files):
    """
    Code-generation harness: Request Python code from the model, execute it via tools,
    and trust the tool output over manual model text. This delegates arithmetic to Python,
    avoiding manual calculation errors that plague the baseline on digit-sum and counting tasks.
    """
    system = files.get("prompts/system.md", "")
    prompt_template = files["prompts/task.md"]
    prompt = prompt_template.replace("{question}", question)
    
    # Request Python code solution
    code_request = f"""{prompt}

Write Python code to solve this problem. The code should output only the numerical answer on its last line of output."""
    
    reply = llm(code_request, system=system)
    
    # Extract Python code from markdown code blocks if present
    code = None
    code_match = re.search(r'```(?:python)?\s*\n(.*?)\n```', reply, re.DOTALL)
    if code_match:
        code = code_match.group(1)
    else:
        # Fallback: attempt to use the entire response as code
        code = reply
    
    # Execute the code via tools
    try:
        output = tools.python(code)
        # Return the last non-empty line of tool output
        lines = [l.strip() for l in output.strip().splitlines() if l.strip()]
        if lines:
            return lines[-1]
    except Exception:
        # If execution fails, fall back to extracting from LLM reply
        pass
    
    # Fallback: return last non-empty line of LLM response
    lines = [l for l in reply.strip().splitlines() if l.strip()]
    return lines[-1] if lines else ""
