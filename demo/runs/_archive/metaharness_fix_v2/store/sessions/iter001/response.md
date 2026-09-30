```json
{
  "iteration": 1,
  "candidates": [
    {
      "name": "code_gen_harness",
      "base_system": "seed",
      "hypothesis": "Delegating arithmetic to Python tool execution will reduce computational errors; the model's manual arithmetic in text (e.g., summing digits) is error-prone, so generating executable code and running it via tools.python() will be more reliable than extracting answers from model text.",
      "axis": "exploitation",
      "components": ["axis:F (model-driven tool usage)"]
    },
    {
      "name": "strict_answer_format_harness",
      "base_system": "seed",
      "hypothesis": "Explicit output formatting reduces extraction errors; instructing the model to output 'ANSWER: <value>' on a dedicated line will encourage consistent, unambiguous responses and make answer extraction more reliable than guessing the last line.",
      "axis": "exploitation",
      "components": ["axis:A (prompt template)", "axis:C (answer extraction/retrieval algorithm)"]
    }
  ]
}
```

=== FILE: agents/code_gen_harness/harness.py ===
```python
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
```

=== FILE: agents/code_gen_harness/prompts/system.md ===
You are a helpful assistant.

=== FILE: agents/code_gen_harness/prompts/task.md ===
{question}

=== FILE: agents/strict_answer_format_harness/harness.py ===
```python
def solve(question, llm, tools, files):
    """
    Strict format harness: Use an explicit output format ('ANSWER: <value>') in the prompt
    and extract the answer by looking for that marker. This encourages consistent model behavior
    and removes ambiguity in answer extraction, reducing errors on tasks where formatting varies
    or the model outputs multiple numbers.
    """
    system = files.get("prompts/system.md", "")
    prompt_template = files["prompts/task.md"]
    prompt = prompt_template.replace("{question}", question)
    
    # Request answer in explicit format
    strict_prompt = f"""{prompt}

Solve this problem step by step. When you have your final numerical answer, output it on a line in this exact format:

ANSWER: <value>

Do not add any text before or after the answer on that line."""
    
    reply = llm(strict_prompt, system=system)
    
    # Extract the ANSWER: line
    for line in reply.strip().splitlines():
        line_stripped = line.strip()
        if line_stripped.startswith("ANSWER:"):
            answer = line_stripped[len("ANSWER:"):].strip()
            if answer:
                return answer
    
    # Fallback: return last non-empty line
    lines = [l for l in reply.strip().splitlines() if l.strip()]
    return lines[-1] if lines else ""
```

=== FILE: agents/strict_answer_format_harness/prompts/system.md ===
You are a helpful assistant.

=== FILE: agents/strict_answer_format_harness/prompts/task.md ===
{question}