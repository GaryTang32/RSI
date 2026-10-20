# Iteration 3: Meta-Harness Evolution

Looking at the history, I notice that **hybrid_code_text_harness achieved 100% on all 12 numeric tasks** while maintaining reasonable cost (3249 tokens avg). The Pareto frontier is now dominated by this perfect system. For iteration 3, I must propose two genuinely different mechanisms—not parameter tweaks—that either reduce cost without sacrificing accuracy or explore alternatives that might transfer better to unfamiliar task types.

## Step 0: Missing Post-Eval Report

I notice iteration 2 has no report yet. Here it is:

=== FILE: reports/iter002.md ===
# Iteration 2 Report

## Summary
Two candidates tested: **verification_harness** (two-pass verification) and **hybrid_code_text_harness** (reasoning + code).

## Results
- **verification_harness**: 58.3% avg (+16.6Δ from seed), 9219.75 context cost
  - Succeeded on 7/12 tasks (modest improvement over seed's 4/12)
  - Two LLM calls created overhead without consistent error recovery
  - Root cause: Re-verification often confirms incorrect answers rather than catching them
  
- **hybrid_code_text_harness**: **100% (+66.7Δ)**, 3249.0 context cost
  - Perfect on all 12 numeric tasks
  - Mechanism: Separate reasoning (narrative) from code (executable block)
  - Clean regex extraction of code blocks; graceful fallback to ANSWER: marker

## Key Observations
1. **Verification doesn't fix bad reasoning**: If the first pass is wrong, the second pass often confirms the error
2. **Separation of concerns beats enforcement**: Asking for "reasoning + code block" is more effective than strict format rules
3. **Arithmetic via Python is correct**: Code execution handles all digit sums, modular arithmetic, and bit counts perfectly
4. **Single-pass + tool is efficient**: 1 LLM call + 1 tool call is faster than multi-pass reasoning

## Takeaway for Iteration 3
The frontier is now optimal on numeric tasks (100%). Explore: (1) Cost reduction by extracting task-specific context, or (2) Alternative mechanisms (self-critique, staged generation) that might generalize to non-numeric tasks or catch edge cases. Avoid repeating axes F, C, B—try A, E, D instead.

---

## Step 1–3: Hypotheses and Mechanism Design

I have studied the traces and top candidates. Here are two falsifiable hypotheses targeting different mechanisms:

### **Hypothesis 1: Pre-extracted context improves code generation efficiency**
Numeric problems always embed key quantities (factorials, moduli, digit counts). If we extract these quantities and operation types from the problem statement itself, then feed them back as structured context to the model, code generation becomes faster and more accurate. The model doesn't waste tokens re-discovering what the problem is asking.

*Example (evolve-numeric-007: digit sum of 64!):*
- Current hybrid: Full reasoning, then code
- This variant: Extract ["64", "factorial", "digit sum"] from question → include as hints → model generates code faster
- Expected: Same 100% accuracy, but fewer reasoning tokens (efficiency gain)

**Axes**: A (augmented prompt structure) + B (extracted quantities as state)

### **Hypothesis 2: Self-critique before execution catches code generation bugs**
Hybrid's strength is code execution, but occasional syntax or logic errors still occur (e.g., off-by-one, missing imports). If we ask the model to explicitly critique its own generated code *before* execution, it can catch bugs and regenerate, creating a feedback loop. This is especially valuable on complex problems where the first code attempt may have subtle errors.

*Example (evolve-numeric-008: 34462116119303 mod 650):*
- Hybrid generated code and executed (happened to work)
- This variant: Generate code → model critiques for correctness → re-generates if needed → execute
- Expected: Same/better accuracy, but with explicit error recovery

**Axes**: E (critique as update trigger) + F (model-written critique and potential fix)

---

## Step 4: Implementation

```json
{
  "iteration": 3,
  "candidates": [
    {
      "name": "context_extracted_code_harness",
      "base_system": "hybrid_code_text_harness",
      "hypothesis": "Pre-extracting key quantities and operations from the problem statement and including them as structured context improves code generation efficiency and reduces token usage without sacrificing correctness.",
      "axis": "exploitation",
      "components": ["axis:A (augmented prompt)", "axis:B (extracted context state)", "axis:D (resource efficiency)"]
    },
    {
      "name": "self_critique_code_harness",
      "base_system": "hybrid_code_text_harness",
      "hypothesis": "Adding an explicit self-critique step after code generation allows the model to identify and fix bugs before execution, improving robustness on complex problems and reducing error rates on edge cases.",
      "axis": "exploration",
      "components": ["axis:E (critique as update trigger)", "axis:F (model-written critique loop)"]
    }
  ]
}
```

---

=== FILE: agents/context_extracted_code_harness/harness.py ===
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

=== FILE: agents/context_extracted_code_harness/src/prompts/system.md ===
You are a helpful assistant.

=== FILE: agents/context_extracted_code_harness/src/prompts/task.md ===
{question}

---

=== FILE: agents/self_critique_code_harness/harness.py ===
import re

def solve(question, llm, tools, files):
    """
    Self-critique code harness: Generate code, ask the model to critique it for bugs,
    optionally regenerate if issues are found, then execute. This two-stage LLM + one-stage
    tool approach improves robustness by catching code generation errors before execution.
    """
    system = files.get("prompts/system.md", "")
    prompt_template = files["prompts/task.md"]
    prompt = prompt_template.replace("{question}", question)
    
    # ===== STAGE 1: Generate code =====
    generation_prompt = f"""{prompt}

Please solve this problem by providing Python code in a ```python code block.
The code must output ONLY the final numerical answer (one line, no text)."""
    
    reply1 = llm(generation_prompt, system=system)
    
    # Extract first code block
    code_match = re.search(r'```(?:python)?\s*\n(.*?)\n```', reply1, re.DOTALL)
    if not code_match:
        # Fallback: try to find ANSWER: marker or return last line
        for line in reply1.strip().splitlines():
            line_stripped = line.strip()
            if line_stripped.startswith("ANSWER:"):
                return line_stripped[len("ANSWER:"):].strip()
        lines = [l for l in reply1.strip().splitlines() if l.strip()]
        return lines[-1] if lines else ""
    
    code = code_match.group(1)
    
    # ===== STAGE 2: Self-critique the code =====
    critique_prompt = f"""{prompt}

I wrote this Python code to solve the problem:

```python
{code}
```

Please review this code for:
1. Logic correctness (does it solve the problem?)
2. Potential bugs or edge cases
3. Syntax errors or missing imports

If you find any issues, provide corrected code in a ```python code block.
If the code looks correct, simply state "Code is correct." (no code block needed).

Be concise."""
    
    reply2 = llm(critique_prompt, system=system)
    
    # Check if model provided corrected code
    corrected_match = re.search(r'```(?:python)?\s*\n(.*?)\n```', reply2, re.DOTALL)
    if corrected_match:
        code = corrected_match.group(1)
    # else: use original code (model said it was correct or couldn't fix)
    
    # ===== STAGE 3: Execute the (original or corrected) code =====
    try:
        output = tools.python(code)
        lines = [l.strip() for l in output.strip().splitlines() if l.strip()]
        if lines:
            return lines[-1]
    except Exception:
        pass
    
    # Fallback: Look for ANSWER: marker in critique response
    for line in reply2.strip().splitlines():
        line_stripped = line.strip()
        if line_stripped.startswith("ANSWER:"):
            answer = line_stripped[len("ANSWER:"):].strip()
            if answer:
                return answer
    
    # Last resort: return last non-empty line from critique
    lines = [l for l in reply2.strip().splitlines() if l.strip()]
    return lines[-1] if lines else ""

=== FILE: agents/self_critique_code_harness/src/prompts/system.md ===
You are a helpful assistant.

=== FILE: agents/self_critique_code_harness/src/prompts/task.md ===
{question}