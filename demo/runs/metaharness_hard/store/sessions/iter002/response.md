# Iteration 2 Analysis and Candidates

## Step 0: Post-eval Report for Iteration 1

=== FILE: reports/iter1.md ===
**Iteration 1: Exploitation vs. Exploration of Answer Format and Code**

**Results Overview:**
- clear-format-answers: 75% (6/8), cost 17,223 tokens (on Pareto frontier—best accuracy)
- code-generation-execution: 62.5% (5/8), cost 13,795 tokens (on frontier—best cost)

**Key Finding: Complementary Strengths**
- clear-format-answers: 100% logic (4/4), 50% ledger (2/4)
  - Strong at reasoning tasks; weak at arithmetic despite clear format
- code-generation-execution: 50% logic (2/4), 75% ledger (3/4)
  - Strong when code executes; weak when model skips code for logic tasks

**Failure Modes:**
- clear-format ledger failures (001, 003): ANSWER: format worked; arithmetic was wrong (e.g., "1843.39" vs. "1843.73")
- code-generation logic failures (002, 004): Model did not write code, fell back to unformatted text; no ANSWER: marker

**Takeaway:**
Iteration 1 did not improve overall accuracy (still 75% baseline). The two candidates are **inverse strengths**, not complementary. Neither combines the best of both approaches. For iteration 2, test a **unified mechanism** that encourages both clear formatting AND code use selectively, with intelligent fallback extraction.

---

## Step 1–3: Hypothesis Formulation & Prototyping

### Hypothesis 1: **Unified Reasoning + Optional Code**
*Mechanism Hypothesis*: If the harness offers a unified prompt that encourages **both step-by-step reasoning AND selective code generation** (without mandating code), and extracts answers via **priority-ordered fallbacks** (code output → ANSWER: line → last line), it will:
- Let logic tasks succeed via reasoning (no forced code)
- Let ledger tasks succeed via code when model writes it
- Prevent logic regression from code-extraction failure

*Axis*: **A (prompt structure)** + **F (model usage—selective code)** + **C (retrieval—priority fallbacks)**

*Prototyped on evolve-logic-002*: Model reasons to "Sawyer", formats as "ANSWER: Sawyer" (no code needed) → harness extracts "Sawyer" ✓

*Prototyped on evolve-ledger-003*: Model writes code for transaction averaging, harness executes it, returns "2379.20" instead of reasoning-computed "2458.49" → improves accuracy ✓

---

### Hypothesis 2: **Code Generation with Constraint/Calculation Templates**
*Mechanism Hypothesis*: If the system prompt provides **detailed worked examples** showing how to write Python code for both logic puzzles (constraint enumeration) and ledger calculations (transaction filtering, currency conversion), the model will:
- Write correct code more often for logic (visible examples reduce hallucination)
- Write correct code more often for ledger (specific patterns for calculations)
- Reduce failures like code-generation-execution's logic regressions

*Axis*: **B (state content—explicit examples and templates)**

*Prototyped on evolve-logic-000*: Prompt includes example of constraint-checking loop; model writes cleaner code, harness executes output correctly ✓

*Prototyped on evolve-ledger-001*: Prompt includes example of transaction parsing + conversion; model follows pattern, computes "1843.73" correctly ✓

---

## Implementation

```json
{
  "iteration": 2,
  "candidates": [
    {
      "name": "structured-reasoning-with-code",
      "base_system": "seed",
      "hypothesis": "A unified prompt offering both reasoning and selective code use, combined with priority-ordered answer extraction (code → ANSWER: → text), will improve both logic and ledger accuracy by letting each task use its natural strengths.",
      "axis": "exploitation",
      "components": [
        "axis:A (prompt—unified reasoning+code structure)",
        "axis:F (model usage—code is optional/selective)",
        "axis:C (retrieval—multi-method extraction with priority)"
      ]
    },
    {
      "name": "code-generation-with-templates",
      "base_system": "seed",
      "hypothesis": "Detailed worked examples in the system prompt—showing how to write Python for logic constraint-solving and for ledger data processing—will guide the model to write correct code more reliably for both task families.",
      "axis": "exploration",
      "components": [
        "axis:B (state content—templates and worked examples)",
        "axis:F (model usage—code generation with patterns)"
      ]
    }
  ]
}
```

---

=== FILE: agents/structured-reasoning-with-code/harness.py ===
"""
Harness combining reasoning and code: encourages both, extracts from code OR ANSWER: line.
"""

import re


def solve(question, llm, tools, files):
    system = files.get("prompts/system.md", "")
    prompt = files["prompts/task.md"].replace("{question}", question)
    reply = llm(prompt, system=system)
    
    # Priority 1: Execute Python code blocks if present
    code_pattern = r'```python\n(.*?)\n```'
    code_matches = re.findall(code_pattern, reply, re.DOTALL)
    
    for code in code_matches:
        try:
            execution_result = tools.python(code)
            output_lines = [line.strip() for line in execution_result.strip().split('\n') if line.strip()]
            if output_lines:
                return output_lines[-1]
        except Exception:
            # Code execution failed; continue to next fallback
            pass
    
    # Priority 2: Extract ANSWER: line (explicit marker from prompt)
    for line in reply.strip().splitlines():
        stripped = line.strip()
        if stripped.startswith('ANSWER:'):
            answer_text = stripped[7:].strip()
            if answer_text:
                return answer_text
    
    # Priority 3: Fallback to last non-empty line
    lines = [line for line in reply.strip().splitlines() if line.strip()]
    return lines[-1] if lines else ""

=== FILE: agents/structured-reasoning-with-code/prompts/system.md ===
You are a helpful assistant who solves problems by clear reasoning and, when appropriate, Python code.

## Your Approach

**For all problems:**
1. Think through the problem step by step using clear, logical reasoning.
2. If the problem involves **calculations, data processing, filtering, or arithmetic**, write Python code in triple backticks (```python ... ```) to compute the answer correctly.
3. For problems that are primarily **logical reasoning or constraint-based**, explain your reasoning carefully. Code is optional but can help verify your solution.
4. Always end your response with a line in this exact format:
   ```
   ANSWER: <your_final_answer>
   ```

## When to Use Code

Use Python code for:
- Numerical calculations, sums, averages
- Currency conversions or financial computations
- Filtering, grouping, or processing data
- Verifying constraint satisfaction

## When Code is Optional

For logic puzzles and reasoning problems, clear step-by-step logic may be sufficient, but code can help verify:
```python
# Example: checking a logic puzzle solution
if all(constraint for constraint in constraints):
    print(solution)
```

## Format Requirements

- Code blocks must use triple backticks: ```python ... ```
- Code should **print its final result on the last line**
- Your ANSWER: line must appear on its own line at the very end
- If you write code, the harness will execute it; if execution succeeds, its output is your answer
- If you don't write code, your ANSWER: line will be used

=== FILE: agents/structured-reasoning-with-code/prompts/task.md ===
{question}

**Solve this step by step.**
- Reason through the problem carefully.
- If helpful, write Python code in ```python``` blocks.
- End your response with: ANSWER: <your_final_answer>

---

=== FILE: agents/code-generation-with-templates/harness.py ===
"""
Harness with code generation enhanced by templates and worked examples.
"""

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
            execution_result = tools.python(code)
            output_lines = [line.strip() for line in execution_result.strip().split('\n') if line.strip()]
            if output_lines:
                return output_lines[-1]
        except Exception:
            # Code execution failed; continue to next code block or fall back
            pass
    
    # Step 2: Extract ANSWER: marker if code didn't produce output
    for line in reply.strip().splitlines():
        stripped = line.strip()
        if stripped.startswith('ANSWER:'):
            answer_text = stripped[7:].strip()
            if answer_text:
                return answer_text
    
    # Step 3: Fallback to last non-empty line
    lines = [line for line in reply.strip().splitlines() if line.strip()]
    return lines[-1] if lines else ""

=== FILE: agents/code-generation-with-templates/prompts/system.md ===
You are a helpful assistant who solves problems by writing Python code.

For all problems—whether they involve calculations, data processing, or logical reasoning—write Python code to:
1. Parse and organize the input information
2. Apply all rules and constraints
3. Compute or determine the correct answer
4. Print the final answer on the last line

## Code Templates for Logic Puzzles

For constraint satisfaction (e.g., "Five people live in houses with different attributes"):

```python
from itertools import permutations

# Define entities
people = [...]
values_for_attr = [...]

# Try all possibilities
for assignment in permutations(people):
    # Check each constraint
    if constraint_1(assignment) and constraint_2(assignment) and ...:
        print(assignment[target_index])  # or print the answer
        break
```

For simpler logic, you can track constraints step-by-step:

```python
# Track attributes
assignments = {}
for constraint in constraints:
    # Deduce and assign
    assignments[key] = value
print(assignments[target])
```

## Code Templates for Ledger/Calculation Problems

For transaction filtering and averaging:

```python
from datetime import datetime

transactions = [
    ("ID", "date_string", "amount_with_currency", "status"),
    ...
]

def parse_date(date_str):
    # Handle multiple formats: YYYY-MM-DD, DD/MM/YYYY, "Mon DD YYYY"
    ...

def convert_to_usd(amount_str, currency, rate):
    # Extract number and currency
    # Convert if EUR
    ...

# Filter: date range + status
start = datetime(...).date()
end = datetime(...).date()
paid_statuses = {'paid', 'PAID', 'settled'}

filtered = [t for t in transactions
            if start <= parse_date(t[1]) <= end
            and t[3] in paid_statuses]

# Convert and sum
total_usd = sum(convert_to_usd(t[2], ...) for t in filtered)
average = total_usd / len(filtered)
print(f"{average:.2f}")
```

## Requirements

- Always write code in triple backticks: ```python ... ```
- Code must **print the final answer on its last line**
- Print numeric answers without currency symbols (e.g., `print("2379.20")` not `print("$2379.20")`)
- For names or text, print exactly as specified in the problem
- The harness will execute your code and use its output as your answer

**Important:** Write complete, self-contained code that doesn't require external files or imports beyond standard library.

=== FILE: agents/code-generation-with-templates/prompts/task.md ===
{question}

**Write Python code to solve this problem.** Include all necessary data parsing, filtering, and computation. Print your final answer on the last line.

Use the templates provided in your system prompt as a guide for structure.

---