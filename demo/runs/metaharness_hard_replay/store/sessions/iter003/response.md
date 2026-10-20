# Iteration 3: Evolution Report & Candidates

## Step 0: Post-Eval Reports

=== FILE: reports/iter1.md ===
**Iteration 1: Format vs. Code Trade-off**

**Results:** No improvement (75% baseline maintained), but revealed complementary mechanisms.
- clear-format-answers: 75% (4/4 logic, 2/4 ledger)
  - ANSWER: prefix eliminated extraction ambiguity on logic tasks
  - Ledger arithmetic still wrong: "1843.39" vs. expected "1843.73" (rounding/mental-math error)
  
- code-generation-execution: 62.5% (2/4 logic, 3/4 ledger)
  - Ledger improved via code execution (3 passes)
  - Logic regressed: model skipped code on logic-002, logic-004, fell back to unformatted text

**Key Finding:** The two systems are inverse strengths, not complementary. Neither combines both approaches well.

**Takeaway:** Iteration 2 should unify mechanisms—either detect task type (logic vs. data) or teach conditional code use. Avoid template over-specification that forces code on logic puzzles.

=== FILE: reports/iter2.md ===
**Iteration 2: Unification Attempts Failed; Ledger Excellence Emerged**

**Results:** Still 62.5% overall; no improvement.

- structured-reasoning-with-code: 62.5% (2/4 logic, 3/4 ledger)
  - Multi-priority extraction worked (code → ANSWER: → last)
  - Logic still regressed (50%) despite optional code
  - Ledger improved slightly (75%)
  
- code-generation-with-templates: 62.5% (1/4 logic, 4/4 ledger) ⭐ **Ledger Perfect**
  - Templates pushed model to write code on ledger: 100% success
  - Logic catastrophically failed (25%): templates over-encouraged code on constraint problems
  - Cost 17,874 (expensive)

**Critical Observation:** code-generation-with-templates achieved 100% ledger (all 4 tasks pass) but only 25% logic. This is the opposite problem from clear-format-answers (100% logic, 50% ledger).

**Takeaway:** The frontier now has inverse systems:
- clear-format-answers: Best logic reasoning (100%), weak ledger arithmetic (50%)
- code-generation-execution: Best cost (13,795 tokens), balanced 62.5%

If we could combine ledger-code strength (from templates) with logic-reasoning strength (from clear-format), we'd hit ~100% overall. The mechanism is **task-aware routing**.

---

## Step 1-3: Hypothesis Formulation, Prototyping, and Implementation

### Hypothesis 1: Task-Type Routing (NEW AXIS E)

**Claim (falsifiable):** The harness can detect whether a question is logic-heavy (constraint puzzle) or data-heavy (transaction/calculation), and apply the strategy most suited to each task type, combining clear-format-answers' logic strength with code-generation-with-templates' ledger perfection.

**Mechanism:** 
1. Analyze question text for keywords (logic: "clue", "person", "house"; data: "transaction", "average", "convert")
2. Route to `solve_logic()` or `solve_data()` 
3. `solve_logic()`: Use reasoning-only prompt + ANSWER: marker (like clear-format-answers)
4. `solve_data()`: Use code-first prompt with calculation templates (like code-generation-with-templates)
5. Extraction: code output → ANSWER: → last line (unified fallback)

**Why this works on traces:**
- evolve-logic-000: Keywords "five houses", "clue", "person" → logic path → reasoning + "ANSWER: Harper" ✓
- evolve-ledger-001: Keywords "transaction", "average", "convert", "USD", "EUR" → data path → code execution → "1843.73" (from code, not mental math) ✓

**Axis:** E (learning/update trigger—task-based routing is a new decision mechanism)

---

### Hypothesis 2: Step-Wise Validation Chain (Axes B + E)

**Claim:** By requiring the model to break down problems into explicit verification steps—where each step includes an inline Python validation block—the harness catches arithmetic errors early and forces structured reasoning on both logic and data tasks.

**Mechanism:**
1. System prompt teaches a "structured reasoning with validation" format:
   - Break problem into labeled steps (Step 1, Step 2, ...)
   - After each reasoning step, include a Python block that validates or computes the intermediate result
   - Final answer MUST be stated with ANSWER: marker
2. Model writes e.g.:
   ```
   Step 1: Identify paid transactions in date range X-Y
   [Reasoning...]
   ```python
   # Validate: count transactions in range
   filtered = [t for t in transactions if ...]
   print(f"Filtered to {len(filtered)} transactions")
   ```
   
   Step 2: Convert EUR to USD
   [Reasoning...]
   ```python
   # Validate: conversion
   total = sum(amount * rate for ...)
   print(f"Total USD: {total:.2f}")
   ```
   
   ANSWER: [final]
   ```
3. Harness: Execute all Python blocks, capture intermediate outputs, use last code output if present, else ANSWER: marker

**Why this works:**
- Data tasks: Model reasons about parsing/filtering, then validates each step with code. Arithmetic checked step-by-step.
- Logic tasks: Model reasons about constraints, validates constraint satisfaction with Python. No forced code, just structured checkpoints.
- Both: Forces model to think through problem carefully before stating final answer

**Axis:** B (state—step-validation structure taught in prompt) + E (learning—each step is a verification trigger)

---

## Implementation

```json
{
  "iteration": 3,
  "candidates": [
    {
      "name": "hybrid-task-router",
      "base_system": "seed",
      "hypothesis": "Detecting task type (logic vs. data) and routing to specialized prompts will combine clear-format-answers' logic strength (100%) with code-generation-with-templates' ledger strength (100%), improving overall accuracy.",
      "axis": "exploration",
      "components": ["axis:E (task-triggered learning)", "axis:A (conditional prompts)", "axis:F (selective code)"]
    },
    {
      "name": "step-wise-validation",
      "base_system": "clear-format-answers",
      "hypothesis": "Teaching the model to structure reasoning into explicit validation steps—each with inline Python verification—will catch arithmetic errors early and improve both logic and data task accuracy via forced decomposition.",
      "axis": "exploitation",
      "components": ["axis:B (state—step-validation structure)", "axis:E (step-triggered verification)"]
    }
  ]
}
```

---

=== FILE: agents/hybrid-task-router/harness.py ===
"""
Harness with task-type detection and routing to specialized strategies.
- Logic tasks: Reasoning-based with formatted answer requirement
- Data tasks: Code-first with calculation templates
"""

import re


def detect_task_type(question):
    """Detect whether the question is logic-heavy or data-heavy."""
    question_lower = question.lower()
    
    # Keywords indicating data/calculation tasks
    data_keywords = [
        "transaction", "ledger", "average", "sum", "total", "date", "amount",
        "currency", "convert", "rate", "paid", "refund", "filter", "calculate",
        "export", "usd", "eur", "settled", "status", "balance", "accounting"
    ]
    
    # Keywords indicating logic/reasoning tasks
    logic_keywords = [
        "logic puzzle", "clue", "must be", "person", "house", "color", "hobby",
        "pet", "drink", "constraint", "immediately left", "next to", "different",
        "standing in a row", "position"
    ]
    
    data_score = sum(1 for kw in data_keywords if kw in question_lower)
    logic_score = sum(1 for kw in logic_keywords if kw in question_lower)
    
    return "data" if data_score > logic_score else "logic"


def solve(question, llm, tools, files):
    task_type = detect_task_type(question)
    
    if task_type == "data":
        return solve_data(question, llm, tools, files)
    else:
        return solve_logic(question, llm, tools, files)


def solve_data(question, llm, tools, files):
    """Solve data-intensive tasks using code-first approach with templates."""
    system = files.get("prompts/system_data.md", "")
    prompt = files.get("prompts/task_data.md", "").replace("{question}", question)
    reply = llm(prompt, system=system)
    
    # Priority 1: Execute Python code blocks
    code_pattern = r'```python\n(.*?)\n```'
    code_matches = re.findall(code_pattern, reply, re.DOTALL)
    
    for code in code_matches:
        try:
            execution_result = tools.python(code)
            output_lines = [line.strip() for line in execution_result.strip().split('\n') if line.strip()]
            if output_lines:
                return output_lines[-1]
        except Exception:
            pass
    
    # Priority 2: Extract ANSWER: marker
    for line in reply.strip().splitlines():
        stripped = line.strip()
        if stripped.startswith('ANSWER:'):
            answer_text = stripped[7:].strip()
            if answer_text:
                return answer_text
    
    # Priority 3: Last line fallback
    lines = [line for line in reply.strip().splitlines() if line.strip()]
    return lines[-1] if lines else ""


def solve_logic(question, llm, tools, files):
    """Solve logic tasks using step-by-step reasoning with formatted answer."""
    system = files.get("prompts/system_logic.md", "")
    prompt = files.get("prompts/task_logic.md", "").replace("{question}", question)
    reply = llm(prompt, system=system)
    
    # Priority 1: Extract ANSWER: marker (preferred for logic)
    for line in reply.strip().splitlines():
        stripped = line.strip()
        if stripped.startswith('ANSWER:'):
            answer_text = stripped[7:].strip()
            if answer_text:
                return answer_text
    
    # Priority 2: Execute code if present (optional for logic)
    code_pattern = r'```python\n(.*?)\n```'
    code_matches = re.findall(code_pattern, reply, re.DOTALL)
    for code in code_matches:
        try:
            execution_result = tools.python(code)
            output_lines = [line.strip() for line in execution_result.strip().split('\n') if line.strip()]
            if output_lines:
                return output_lines[-1]
        except Exception:
            pass
    
    # Priority 3: Last line fallback
    lines = [line for line in reply.strip().splitlines() if line.strip()]
    return lines[-1] if lines else ""

=== FILE: agents/hybrid-task-router/prompts/system_data.md ===
You are a helpful assistant who solves data-processing and calculation problems by writing Python code.

For all data-intensive problems (involving transactions, calculations, filtering, currency conversion, averaging, ledger analysis, etc.):

1. **Parse and organize the data** in Python, handling multiple date and currency formats
2. **Apply all rules and constraints** from the problem (date ranges, status filters, corrections, conversions)
3. **Compute the result** using Python
4. **Print the final answer** on the last line of your code output

## Code Template for Ledger/Transaction Problems

```python
from datetime import datetime
import re

# Define transactions (parse from the problem statement)
transactions = [
    # (ID, date_string, amount_string, currency, status)
    ...
]

def parse_date(date_str):
    """Handle YYYY-MM-DD, DD/MM/YYYY, and 'Mon DD YYYY' formats."""
    formats = ["%Y-%m-%d", "%d/%m/%Y", "%b %d %Y"]
    for fmt in formats:
        try:
            return datetime.strptime(date_str.strip(), fmt).date()
        except ValueError:
            continue
    return None

def convert_to_usd(amount_str, currency, rate):
    """Extract numeric amount and convert to USD if needed."""
    # Remove currency symbols and parse the number
    amount_str = amount_str.replace('.', '').replace(',', '.')
    amount = float(re.sub(r'[^\d.]', '', amount_str))
    if currency.upper() == 'EUR':
        return amount * rate
    return amount

def is_paid(status):
    """Check if transaction status means paid/settled."""
    return status.strip().lower() in ['paid', 'settled']

# Parse problem rules: extraction rates, date ranges, etc.
USD_RATE = 1.0  # default
EUR_RATE = 1.145  # example; replace with problem-specified rate
start_date = datetime(...).date()  # from problem
end_date = datetime(...).date()    # from problem

# Filter: date range AND paid status
filtered = [t for t in transactions
            if start_date <= parse_date(t[1]) <= end_date
            and is_paid(t[4])]

# Convert all amounts to USD and sum
total_usd = sum(convert_to_usd(t[2], t[3], EUR_RATE) for t in filtered)

# Calculate result (e.g., average)
result = total_usd / len(filtered)

# Print final answer (number only, no currency symbol)
print(f"{result:.2f}")
```

## Requirements

- Always write Python code in triple backticks: ```python ... ```
- Code must **print the final numerical answer on its last line**
- Print numbers without currency symbols (e.g., `2379.20` not `$2,379.20`)
- Embed all data directly in the code (do not reference external files)
- Handle multiple date formats and currency notations correctly
- The harness will execute your code and use its output as your answer

=== FILE: agents/hybrid-task-router/prompts/task_data.md ===
{question}

**Solve this using Python code.**

Write a complete, self-contained Python script that:
1. Parses and processes all input data from the problem
2. Applies all rules, constraints, and conversions as specified
3. Computes the final answer
4. Prints the answer (numbers only, no currency symbols) on the last line

Use the code template from your system prompt as a guide. Ensure your code runs without errors.

=== FILE: agents/hybrid-task-router/prompts/system_logic.md ===
You are a helpful assistant who solves logic puzzles by careful, step-by-step reasoning.

For logic puzzles (constraint-based deduction, puzzle solving):

1. **Work through the constraints and clues systematically**
2. **Derive conclusions** using logical deduction
3. **Track what is possible and what is ruled out** at each step
4. **State your final answer clearly** at the very end

## Format Your Answer

End your response with a line in this exact format:

ANSWER: <your_final_answer>

The answer must:
- Appear on its own line at the very end
- Have no extra text before or after "ANSWER:"
- For names/categories: provide the exact name as specified in the problem (e.g., `Harper`, `Delta`, `Cobalt`)
- For counts: provide a number (e.g., `5`)

## Example Reasoning Structure

- **Direct deductions:** "From clue X, Y must be true."
- **Elimination:** "Z is impossible because..."
- **Inference:** "Since A and B, then C follows."
- **Conclusion:** "Therefore, the answer is..."

Then end with: ANSWER: <answer>

=== FILE: agents/hybrid-task-router/prompts/task_logic.md ===
{question}

**Solve this step by step.**

Work through the logical constraints carefully. Reason clearly about what must be true and what is ruled out. You may optionally write Python code to verify constraints, but step-by-step reasoning is often sufficient.

End your response with a line in this format:
ANSWER: <your_final_answer>

---

=== FILE: agents/step-wise-validation/harness.py ===
"""
Harness combining reasoning with step-wise validation blocks.
Model structures reasoning into explicit steps, each with inline Python verification.
Extracts answer from code output or ANSWER: marker.
"""

import re


def solve(question, llm, tools, files):
    system = files.get("prompts/system.md", "")
    prompt = files.get("prompts/task.md", "").replace("{question}", question)
    reply = llm(prompt, system=system)
    
    # Priority 1: Execute all Python code blocks in order; return last output
    code_pattern = r'```python\n(.*?)\n```'
    code_matches = re.findall(code_pattern, reply, re.DOTALL)
    
    final_code_output = None
    for code in code_matches:
        try:
            execution_result = tools.python(code)
            output_lines = [line.strip() for line in execution_result.strip().split('\n') if line.strip()]
            if output_lines:
                final_code_output = output_lines[-1]
        except Exception:
            # Code execution failed; continue to next block or fallback
            pass
    
    if final_code_output:
        return final_code_output
    
    # Priority 2: Extract ANSWER: marker from text
    for line in reply.strip().splitlines():
        stripped = line.strip()
        if stripped.startswith('ANSWER:'):
            answer_text = stripped[7:].strip()
            if answer_text:
                return answer_text
    
    # Priority 3: Fallback to last non-empty line
    lines = [line for line in reply.strip().splitlines() if line.strip()]
    return lines[-1] if lines else ""

=== FILE: agents/step-wise-validation/prompts/system.md ===
You are a helpful assistant who solves problems by structured reasoning with step-wise validation.

## Your Approach

**For all problems**, use this structure:

1. **Understand the problem** — State what you need to find
2. **Break it into steps** — Label each major reasoning step (Step 1, Step 2, etc.)
3. **At each step:**
   - State your reasoning or constraint deduction
   - Include a small Python validation block to verify that step
4. **Iterate to the final answer**
5. **End with:** `ANSWER: <your_final_answer>`

## Validation Blocks

After each reasoning step, include a Python code block that validates or computes that step's result.

### Example for Data/Calculation Problems:

```
Step 1: Parse and identify all transactions in the date range 2024-05-01 to 2024-06-30 with "paid" status.

[Your reasoning about which transactions qualify...]

```python
# Step 1 validation: Count qualifying transactions
transactions = [...]  # from problem
filtered = [t for t in transactions
            if start_date <= parse_date(t['date']) <= end_date
            and t['status'] in ['paid', 'settled']]
print(f"Filtered to {len(filtered)} transactions")
```

Step 2: Convert EUR amounts to USD using rate 1 EUR = 1.145 USD, and sum all amounts.

[Your reasoning about conversion and summation...]

```python
# Step 2 validation: Compute total
total_usd = sum(convert(t) for t in filtered)
print(f"Total USD: {total_usd:.2f}")
```

Step 3: Calculate the average.

[Final calculation...]

```python
# Step 3 validation: Average
average = total_usd / len(filtered)
print(f"{average:.2f}")
```

ANSWER: 2379.20
```

### Example for Logic Problems:

```
Step 1: From clue X, Y must be in position Z. This means A cannot be in that position.

```python
# Verify constraint: Y in position Z rules out A
if Y_in_position[Z]:
    A_possible_positions = [p for p in range(1,6) if p != Z]
    print(f"A must be in one of: {A_possible_positions}")
```

Step 2: From clue P and Step 1, we can deduce that B is in position Q.

```python
# Verify deduction
if condition_from_step_1 and clue_P:
    print("B in position Q: confirmed")
```

[Continue until you've solved the puzzle...]

ANSWER: Harper
```

## Key Rules

1. **Every step must have both reasoning and a Python validation block**
2. **Each Python block should print intermediate results** (counts, validations, deductions)
3. **Code blocks must be complete and self-contained** (no external dependencies)
4. **Final answer must end with: `ANSWER: <your_answer>`**
5. **The harness will:**
   - Execute all Python blocks in order
   - Use the last block's output as your answer (if code succeeds)
   - Fall back to the ANSWER: line if code doesn't run

## When to Use Code

- **Data/calculations:** Compute intermediate sums, counts, conversions; validate each step
- **Logic puzzles:** Verify constraints; check that deduced facts satisfy all clues
- **Both:** Use code to double-check your reasoning and catch errors early

=== FILE: agents/step-wise-validation/prompts/task.md ===
{question}

**Solve this by structuring your reasoning into explicit steps, each validated with Python.**

Follow this pattern:
1. State your understanding of the problem
2. Break it into logical steps (Step 1, Step 2, etc.)
3. For each step:
   - Explain your reasoning
   - Include a Python code block that validates or computes that step
4. End with: `ANSWER: <your_final_answer>`

The harness will execute your code blocks and use the final output as your answer. Ensure all code is self-contained and complete.