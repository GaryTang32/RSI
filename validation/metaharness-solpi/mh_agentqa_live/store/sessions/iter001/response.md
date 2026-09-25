# Iteration 1 Analysis

Looking at the evolution history, the seed baseline achieves 50% accuracy with 2378.2 average context cost. Let me analyze the failures and successes in the traces:

## Failure Analysis

**Extraction issues (evolve-numeric-004, evolve-numeric-007):**
- Task 004: Expected '234', last line was verification text `"We can verify: 234 = 0 (mod 2) ✓..."`—the answer appears mid-output, not at end
- Task 007: Expected '435', extracted `"remainder **70**"` instead—last-line extraction grabbed verification, not the primary answer

**Computation issues (evolve-numeric-001, evolve-numeric-005, evolve-numeric-006):**
- Tasks 001, 005, 006: Model reasoned but computed wrong numeric results

## Hypotheses & Candidates

**Hypothesis 1 (Exploitation):** "Models format final answers with bold markers or place them on clear numeric lines. Extracting from formatted sections before falling back to last-line is more robust than always taking the last line."
- This targets extraction failures (004, 007)
- Mechanism: Priority-based extraction (bold numbers → standalone numbers → last line)

**Hypothesis 2 (Exploration):** "Forcing Python-first computation and extracting from code output, rather than parsing model text, provides cleaner, more verifiable answers. Models know to use tools—we just need to mandate it and extract from execution output."
- This targets computation accuracy
- Mechanism: Explicit code-generation prompt + execution-based extraction (entirely different from text parsing)

```json
{
  "iteration": 1,
  "candidates": [
    {
      "name": "extract_formatted",
      "base_system": "seed",
      "hypothesis": "Models emphasize final answers with **bold** formatting or isolated numbers on clear lines. Priority-based extraction (formatted → standalone numbers → last line) is more reliable than always taking the last line.",
      "axis": "exploitation",
      "components": ["extraction", "pattern-matching", "formatting-aware"]
    },
    {
      "name": "code_first",
      "base_system": "seed",
      "hypothesis": "Mandating Python code generation and extracting from execution output, rather than parsing model text reasoning, ensures numeric accuracy. Computation-first is fundamentally more reliable than text-based reasoning.",
      "axis": "exploration",
      "components": ["code-generation", "execution-extraction", "python-first"]
    }
  ]
}
```

---

## Candidate Files

=== FILE: agents/extract_formatted/harness.py ===
"""
Extract answers using priority: bold-formatted numbers → isolated numbers → last line.
Targets failures where answers are embedded in text or follow verification statements.
"""

import re


def solve(question, llm, tools, files):
    system = files.get("prompts/system.md", "")
    prompt = files["prompts/task.md"].replace("{question}", question)
    reply = llm(prompt, system=system)
    
    # Strategy 1: Extract bold-formatted numbers (highest priority)
    # Matches **number** or **number,number** etc.
    bold_matches = re.findall(r'\*\*(-?[\d,]+(?:\.\d+)?)\*\*', reply)
    if bold_matches:
        # Take the last bold number (usually the final answer)
        last_bold = bold_matches[-1].replace(',', '')
        return last_bold
    
    # Strategy 2: Look for lines containing isolated large numbers or clear statements
    lines = [line.strip() for line in reply.strip().splitlines() if line.strip()]
    for line in reversed(lines):
        # Skip obvious verification/explanation lines
        if any(phrase in line.lower() for phrase in ['verify', 'check', 'confirm', 'calculation check']):
            continue
        
        # Look for standalone numbers (with optional commas)
        numbers = re.findall(r'-?[\d,]+(?:\.\d+)?', line)
        if numbers:
            # Prefer the first substantial number on the line
            candidate = numbers[0].replace(',', '')
            if len(candidate) > 0 and candidate not in ['0', '1']:
                return candidate
    
    # Strategy 3: Fallback to last line (original behavior)
    if lines:
        return lines[-1]
    
    return ""

=== FILE: agents/extract_formatted/prompts/system.md ===
You are a helpful assistant. Provide clear, concise answers. When stating a final numeric answer, emphasize it with bold formatting like **answer**.

=== FILE: agents/extract_formatted/prompts/task.md ===
{question}

=== FILE: agents/code_first/harness.py ===
"""
Code-first solving: mandate Python code generation, execute it, extract result.
Bypasses model text reasoning; ensures answers come from computation.
"""

import re


def solve(question, llm, tools, files):
    system = files.get("prompts/system.md", "")
    
    # Prompt model to write Python code that solves and prints the answer
    code_prompt = f"""Solve this problem by writing Python code:

{question}

Write Python code that:
1. Computes the answer
2. Prints it with: print(ANSWER)

Provide ONLY the Python code, no explanation."""
    
    code_response = llm(code_prompt, system=system)
    
    # Extract Python code (remove markdown wrappers if present)
    code = code_response
    for wrapper in ['```python\n', '```python', '```\n', '```']:
        code = code.replace(wrapper, '')
    code = code.rstrip('`').strip()
    
    # Execute the code
    try:
        output = tools.python(code)
        
        # Extract numbers from output (last number is most likely the answer)
        numbers = re.findall(r'-?[\d,]+(?:\.\d+)?', output)
        if numbers:
            # Take the last substantial number
            result = numbers[-1].replace(',', '')
            return result
    except Exception:
        # If code execution fails, fall back to text-based solve
        pass
    
    # Fallback: solve via text reasoning like seed
    fallback_prompt = files["prompts/task.md"].replace("{question}", question)
    reply = llm(fallback_prompt, system=system)
    lines = [line for line in reply.strip().splitlines() if line.strip()]
    return lines[-1] if lines else ""

=== FILE: agents/code_first/prompts/system.md ===
You are an expert Python programmer and mathematician. You excel at writing correct computational code.

=== FILE: agents/code_first/prompts/task.md ===
{question}