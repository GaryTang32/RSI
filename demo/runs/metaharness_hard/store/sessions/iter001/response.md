# Iteration 1: Meta-Harness Evolution

Looking at the iteration 0 results, I see a clear pattern: **logic tasks pass 100%, but ledger tasks pass only 50%**. Let me diagnose and form hypotheses.

## Step 0: Post-Eval Report (Iteration 0)

=== FILE: reports/iter0.md ===

**Baseline (Seed) System Analysis**

**Results:** 75% overall (6/8 correct)
- Logic tasks (all 4): 1.0 each ✓
- Ledger tasks: 2/4 pass

**Failures:**
- evolve-ledger-005: Expected '2805.10', extracted partial analysis line '- 1 transaction in EUR...'
- evolve-ledger-007: Expected 'Delta', extracted transaction detail '- T1054 (Jul 4): €3,287.94...'

**Root Cause:** The harness returns the last line of the model's response. For logic puzzles this works (model naturally concludes with the answer name). For ledger problems, the model produces detailed analysis and the last line is often a detail, not the final answer. The model has no instruction on how to format the answer.

**Token Cost:** 18,230 avg (wide variance: 14k–24k per task)

**Takeaway:** Need mechanism changes, not parameter tuning. Two axes to explore:
1. **Prompt template (A):** Explicitly instruct model to format final answer
2. **Tool integration (F):** Leverage Python for deterministic computation on data problems

---

## Diagnosis from Traces

Tracing through **evolve-ledger-001 (PASS):**
- Model output ends with: "**Answer: $1,843.73**"
- Harness returns that line → matches expected answer ✓

Tracing through **evolve-ledger-005 (FAIL):**
- Model output ends with: "- 1 transaction in EUR (T1037: 3,877.58 EUR = $4,439.83 USD at 1.145 conversion rate)"
- Harness returns that line → does NOT match expected '2805.10' ✗
- Model clearly did analysis but didn't state a final answer explicitly

## Formulated Hypotheses

**Hypothesis 1 (Exploitation):** "Explicitly instructing the model to place its final answer on the last line with 'ANSWER: ' prefix will eliminate extraction ambiguity and improve accuracy across all task types."
- Mechanism: Modify prompt to enforce answer format
- Axis: **A (prompt template) + B (state content)**

**Hypothesis 2 (Exploration):** "For data-intensive problems, delegating computation to Python code (written by the model, executed by the harness) will reduce arithmetic errors and hallucination."
- Mechanism: Detect code blocks in model output, execute them, use output as answer
- Axis: **F (model usage—code generation) + C (retrieval—code execution)**

---

## Prototyping on Real Examples

### Prototype 1: Clear Answer Format

**evolve-ledger-005 walkthrough:**
- Old prompt: (silent on format)
- Old model output: `[40 lines of analysis] ... - T1037: 3,877.58 EUR = $4,439.83`
- Old harness action: return last line → MISMATCH ✗

- New prompt: "End your response with: ANSWER: <your_final_answer>"
- New model output: `[40 lines of analysis] ... $2,805.10 USD ... ANSWER: 2805.10`
- New harness action: return last line → "ANSWER: 2805.10" → grader sees "ANSWER:" prefix, extracts "2805.10" → MATCH ✓

**Logic task invariant (evolve-logic-000):**
- Model output: `[reasoning] ... **Harper**`
- With new format: `[reasoning] ... **Harper** ... ANSWER: Harper`
- Still works ✓

### Prototype 2: Code Generation + Execution

**evolve-ledger-003 walkthrough:**
- Task: Calculate average of 25 transactions (mix USD, EUR with conversion)
- Old: Model does mental math inline → prone to error
  
- New flow:
  1. Prompt: "Write Python code to solve this"
  2. Model output:
     ```python
     transactions = [...]
     paid = [t for t in transactions if t['status'] in ['paid', 'PAID', 'settled']]
     total_usd = sum(convert(t['amount'], t['currency']) for t in paid)
     print(total_usd / len(paid))
     ```
  3. Harness detects code block → executes with `tools.python()` → captures output "2379.20"
  4. Returns to grader → MATCH ✓

- Logic task (evolve-logic-000): 
  - Model outputs reasoning + code to track constraints
  - Code outputs "Harper"
  - Harness extracts and returns it ✓
  - (Or model just answers normally without code, harness falls back to text extraction)

---

```json
{
  "iteration": 1,
  "candidates": [
    {
      "name": "clear-format-answers",
      "base_system": "seed",
      "hypothesis": "Explicitly instructing the model to format final answers with 'ANSWER: ' prefix on the last line will eliminate extraction ambiguity and improve accuracy on all task types.",
      "axis": "exploitation",
      "components": ["axis:A (prompt template)", "axis:B (state/instruction content)"]
    },
    {
      "name": "code-generation-execution",
      "base_system": "seed",
      "hypothesis": "Having the model generate executable Python code and the harness execute it will reduce arithmetic errors and improve accuracy on data-intensive tasks while remaining general across task types.",
      "axis": "exploration",
      "components": ["axis:F (model usage—code generation)", "axis:C (retrieval—code execution)"]
    }
  ]
}
```

---

=== FILE: agents/clear-format-answers/harness.py ===
```python
"""Harness with explicit answer formatting: returns last line (which should be ANSWER: ...)."""


def solve(question, llm, tools, files):
    system = files.get("prompts/system.md", "")
    prompt = files["prompts/task.md"].replace("{question}", question)
    reply = llm(prompt, system=system)
    lines = [line for line in reply.strip().splitlines() if line.strip()]
    return lines[-1] if lines else ""
```

=== FILE: agents/clear-format-answers/prompts/system.md ===
```
You are a helpful assistant. You solve problems by thinking through them step by step.

CRITICAL: Every response must end with a line in this exact format:
ANSWER: <your_final_answer>

This line will be the ONLY line used to evaluate your correctness. Make sure:
- The answer is clear and unambiguous
- It appears on its own line at the very end of your response
- It contains no extra text or formatting before "ANSWER:"
- For calculations: use numbers or currency (e.g., ANSWER: 2379.20 or ANSWER: $1,843.73)
- For names/categories: use the exact name (e.g., ANSWER: Harper or ANSWER: Delta)
- For counts: use a number (e.g., ANSWER: 5)

Put all your reasoning and analysis before the ANSWER: line.
```

=== FILE: agents/clear-format-answers/prompts/task.md ===
```
{question}

Remember: Your final answer MUST appear on a line by itself in this format at the very end:
ANSWER: <your_final_answer>
```

---

=== FILE: agents/code-generation-execution/harness.py ===
```python
"""Harness with code generation and execution for data-intensive problems."""

import re


def solve(question, llm, tools, files):
    system = files.get("prompts/system.md", "")
    prompt = files["prompts/task.md"].replace("{question}", question)
    reply = llm(prompt, system=system)
    
    # Step 1: Try to find and execute Python code blocks
    code_pattern = r'```python\n(.*?)\n```'
    code_matches = re.findall(code_pattern, reply, re.DOTALL)
    
    for code in code_matches:
        try:
            # Execute the Python code using the provided tools
            execution_result = tools.python(code)
            # Extract the last non-empty line from execution output
            output_lines = [line.strip() for line in execution_result.strip().split('\n') if line.strip()]
            if output_lines:
                # Return the last output line as the answer
                return output_lines[-1]
        except Exception:
            # If code execution fails, continue to next code block or fall back to text extraction
            pass
    
    # Step 2: Fall back to extracting text answer
    # First, look for ANSWER: marker in the response
    for line in reply.strip().splitlines():
        stripped = line.strip()
        if stripped.startswith('ANSWER:'):
            return stripped[7:].strip()
    
    # Last resort: return the last non-empty line
    lines = [line for line in reply.strip().splitlines() if line.strip()]
    return lines[-1] if lines else ""
```

=== FILE: agents/code-generation-execution/prompts/system.md ===
```
You are a helpful assistant who solves problems by thinking carefully and, when useful, writing Python code.

For complex problems—especially those involving data processing, calculations, filtering, or multi-step logic:

1. Think through the problem step by step.
2. If writing code would help, include Python code in triple backticks like this:
   ```python
   # Your code here
   print(final_answer)
   ```
3. Ensure that any code you write prints or outputs the final answer on its last line.
4. The harness will execute your code and use its output as your answer.

For simpler problems or reasoning tasks, you may answer directly without code.

Always end your response with a line starting with "ANSWER: " that states your final answer clearly.

Example response structure:
- Reasoning and analysis
- [Optional: Python code block if needed]
- Final line: ANSWER: <your_final_answer>
```

=== FILE: agents/code-generation-execution/prompts/task.md ===
```
{question}

Approach: Think through this problem step by step. If helpful, write Python code to solve it (in ```python``` blocks). 
End your response with a line: ANSWER: <your_final_answer>

For data-intensive problems, Python code will reduce arithmetic errors.
```