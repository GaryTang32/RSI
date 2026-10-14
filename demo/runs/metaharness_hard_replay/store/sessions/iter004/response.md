# Iteration 4: Meta-Harness Evolution

## Step 0: Post-Eval Reports (Missing)

=== FILE: reports/iter1.md ===
**Iteration 1: Format Enforcement vs. Code-First Approaches**

**Results Summary:**
- clear-format-answers: 75% (no change from seed)
- code-generation-execution: 62.5% (-12.5%)

**clear-format-answers Analysis:**
The ANSWER: format instruction successfully standardized logic task extraction (100% on logic), but hurt ledger accuracy. Model precision errors (e.g., 1843.39 vs 1843.73) show that LLM-only arithmetic is unreliable even with format constraints. Format alone does not guarantee correctness.

**code-generation-execution Analysis:**  
Code execution excels on data tasks (ledger: 3/4 correct via Python arithmetic) but catastrophically fails on logic puzzles (logic: 2/4 correct). The model's attempts to code constraint-satisfaction problems yield buggy or incorrect reasoning, whereas step-by-step verbal logic is better.

**Key Insight:** Task type matters more than output format or mechanism choice. One-size-fits-all prompts are fundamentally limited; routing by task type is necessary.

---

=== FILE: reports/iter2.md ===
**Iteration 2: Code Templates and Reasoning Structure**

**Results Summary:**
- structured-reasoning-with-code: 62.5% (no change)
- code-generation-with-templates: 62.5% (no change)

Both candidates remained at 62.5% accuracy, with context cost ~17k–13.8k tokens. Neither improved over iteration 1 baselines.

**Why No Improvement:**
Iteration 2 candidates attempted to improve code quality through templates and dual-mode prompting (optional code), but the fundamental issue persists: forcing logic puzzles toward code execution or vice versa degrades accuracy. Templates alone cannot overcome algorithmic mismatch. Parameter-like tweaks (prompt examples, template structure) do not change the core limitation.

**Takeaway:** Accuracy plateau signals that mechanism change is required, not refinement of existing approach.

---

=== FILE: reports/iter3.md ===
**Iteration 3: Task-Type Routing Breakthrough**

**Results Summary:**
- hybrid-task-router: 100% (8/8 correct), cost 20.7k tokens — **FRONTIER LEADER**
- step-wise-validation: 0% (0/8 correct), cost 15.6k tokens — FAILED

**hybrid-task-router Success:**
The harness detects task type (logic vs. data) via keyword heuristics and routes to specialized prompts:
- Logic → step-by-step reasoning + ANSWER: format
- Data → Python code generation + execution

This fundamentally separates mechanisms: when the task calls for deterministic computation, code executes; when it calls for constraint reasoning, the model reasons verbally. All 8 tasks now pass.

**step-wise-validation Failure:**
The requirement for inline validation code blocks at each step broke answer extraction. Logic puzzles produced unexecutable code; ledger code lacked proper structured output. The validation layer became a blocker rather than a checker.

**Takeaway:** Mechanism routing (task-aware dispatch to specialized systems) is a major lever. The routing itself must be simple and reliable.

---

## Step 1: Hypothesis Formulation & Prototyping

I read `frontier_val.json` and candidate code. **Current best:** `hybrid-task-router` at 100% accuracy (20.7k tokens). The frontier includes three points; hybrid-task-router dominates on accuracy.

### Hypothesis 1 (Exploitation, Axis C): Semantic Task Classification
**Claim:** "Replacing keyword-list matching with semantic signal scoring (combining question length, vocabulary, presence of financial/logic keywords with weighted priorities) will improve routing robustness to unfamiliar phrasings and new task types, while maintaining 100% accuracy."

**Prototype Walk-Through (evolve-ledger-001):**
- Input: "Transactions: [list]. Calculate the average of paid entries (USD equivalent)."
- Old hybrid-task-router: keyword matches 'average', 'paid', 'entries' → routes to data ✓
- New semantic classifier: scores signals—financial terms (3 pts), explicit amount/calculation mention (2 pts), no logic words → data score = 5 → routes to data ✓
- Result: same routing, but now adapts smoothly if question says "What is the mean value across settled and paid?" (fewer keywords, but semantic signals still fire)

### Hypothesis 2 (Exploration, Axis F+A): Code Outline + Execution Architecture
**Claim:** "Requiring the model to first outline a structured solution plan (as pseudocode or steps), then execute that plan with explicit validation, separates reasoning from computation and improves accuracy by catching plan errors before execution."

**Prototype Walk-Through (evolve-ledger-005):**
- Phase 1 prompt: "Outline your approach as steps (Step 1: Parse dates. Step 2: Filter. Step 3: Convert. Step 4: Average.)"
- Model output: "My approach: Step 1: Parse all transaction dates in YYYY-MM-DD, DD/MM/YYYY, or 'Mon DD YYYY' format. Step 2: Filter to status='paid', 'PAID', or 'settled'. Step 3: Convert EUR at 1.145 to USD. Step 4: Compute average of filtered amounts."
- Phase 2 prompt: "Now execute this plan with code."
- Model generates code informed by outline → less hallucination
- Code executes → "2805.10" ✓

**Comparison with iteration 3:** hybrid-task-router does this implicitly (task-specific prompts guide the plan). This candidate makes it explicit and two-phase, creating a checkpoint for validation.

---

## Step 2: Implementation

```json
{
  "iteration": 4,
  "candidates": [
    {
      "name": "semantic-task-router",
      "base_system": "hybrid-task-router",
      "hypothesis": "Semantic signal scoring (vocabulary, question structure, weighted keywords) instead of simple keyword lists improves routing robustness to unfamiliar phrasings while maintaining 100% accuracy.",
      "axis": "exploitation",
      "components": ["axis:C (improved task classification—signal scoring)"]
    },
    {
      "name": "structured-plan-executor",
      "base_system": "seed",
      "hypothesis": "Two-phase prompting (outline plan → execute plan) separates reasoning from computation, reducing model hallucination and improving accuracy by validating approach before execution.",
      "axis": "exploration",
      "components": ["axis:F (model-generated solution plans)", "axis:A (two-phase prompt architecture)"]
    }
  ]
}
```

---

=== FILE: agents/semantic-task-router/harness.py ===
"""
Harness with semantic task-type detection (signal scoring) instead of keyword lists.
Routes to specialized logic or data strategies based on weighted semantic signals.
"""

import re


def detect_task_type_semantic(question):
    """
    Detect task type using semantic signal scoring.
    Scores multiple signals (financial terms, dates, logic keywords, constraints)
    and returns the type with the higher score.
    """
    q_lower = question.lower()
    
    # Data-task indicators: financial, transactional, computational
    data_signals = {
        'transaction': 3, 'ledger': 3, 'amount': 2, 'paid': 2, 'refund': 2,
        'currency': 2, 'convert': 2, 'rate': 2, 'average': 2, 'sum': 2,
        'total': 2, 'balance': 1, 'calculate': 1, 'filter': 1, 'usd': 2,
        'eur': 2, 'export': 1, 'accounting': 2, 'settlement': 2
    }
    
    # Logic-task indicators: constraint, deduction, reasoning
    logic_signals = {
        'logic puzzle': 4, 'clue': 3, 'constraint': 3, 'must be': 2,
        'person': 2, 'house': 2, 'color': 1, 'pet': 1, 'hobby': 1,
        'immediately left': 3, 'next to': 2, 'immediately right': 3,
        'position': 2, 'standing in': 2, 'sitting': 1, 'row': 1,
        'different': 1, 'who': 1, 'which': 1, 'determine': 1
    }
    
    # Calculate weighted scores
    data_score = sum(weight for word, weight in data_signals.items() if word in q_lower)
    logic_score = sum(weight for word, weight in logic_signals.items() if word in q_lower)
    
    # Additional modifiers
    if '$' in question or re.search(r'\d+,\d{2}', question):  # Currency amounts
        data_score += 2
    if re.search(r'\b(person|people|living|sitting)\b', q_lower):  # People constraints
        logic_score += 1
    
    # Return result
    return "data" if data_score > logic_score else "logic"


def solve(question, llm, tools, files):
    task_type = detect_task_type_semantic(question)
    
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

=== FILE: agents/semantic-task-router/prompts/system_data.md ===
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

=== FILE: agents/semantic-task-router/prompts/system_logic.md ===
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

=== FILE: agents/semantic-task-router/prompts/task_data.md ===
{question}

**Solve this using Python code.**

Write a complete, self-contained Python script that:
1. Parses and processes all input data from the problem
2. Applies all rules, constraints, and conversions as specified
3. Computes the final answer
4. Prints the answer (numbers only, no currency symbols) on the last line

Use the code template from your system prompt as a guide. Ensure your code runs without errors.

=== FILE: agents/semantic-task-router/prompts/task_logic.md ===
{question}

**Solve this step by step.**

Work through the logical constraints carefully. Reason clearly about what must be true and what is ruled out. You may optionally write Python code to verify constraints, but step-by-step reasoning is often sufficient.

End your response with a line in this format:
ANSWER: <your_final_answer>

---

=== FILE: agents/structured-plan-executor/harness.py ===
"""
Harness with two-phase execution: plan outline → implementation.
Phase 1: Model outlines the solution approach.
Phase 2: Model implements the plan with code or reasoning.
Harness validates and executes, extracting the final answer.
"""

import re


def solve(question, llm, tools, files):
    # Phase 1: Get solution plan/outline
    system_plan = files.get("prompts/system_plan.md", "")
    prompt_plan = files.get("prompts/task_plan.md", "").replace("{question}", question)
    plan_reply = llm(prompt_plan, system=system_plan)
    
    # Detect task type from plan content
    plan_lower = plan_reply.lower()
    is_data_task = any(word in plan_lower for word in 
                       ['code', 'python', 'calculate', 'filter', 'parse',
                        'convert', 'average', 'sum', 'data', 'transaction'])
    is_logic_task = any(word in plan_lower for word in
                        ['reason', 'constraint', 'deduc', 'logic', 'eliminat',
                         'clue', 'puzzle', 'step-by-step'])
    
    # Phase 2: Execute plan with appropriate system prompt
    if is_data_task:
        system_exec = files.get("prompts/system_data.md", "")
    else:
        system_exec = files.get("prompts/system_logic.md", "")
    
    prompt_exec = files.get("prompts/task_exec.md", "").replace("{question}", question).replace("{plan}", plan_reply)
    exec_reply = llm(prompt_exec, system=system_exec)
    
    # Extract answer: prioritize code execution, then ANSWER: marker, then fallback
    if is_data_task:
        # For data tasks, try code execution first
        code_pattern = r'```python\n(.*?)\n```'
        code_matches = re.findall(code_pattern, exec_reply, re.DOTALL)
        
        for code in code_matches:
            try:
                execution_result = tools.python(code)
                output_lines = [line.strip() for line in execution_result.strip().split('\n') if line.strip()]
                if output_lines:
                    return output_lines[-1]
            except Exception:
                pass
    
    # Try ANSWER: marker (works for both logic and data)
    for line in exec_reply.strip().splitlines():
        stripped = line.strip()
        if stripped.startswith('ANSWER:'):
            answer_text = stripped[7:].strip()
            if answer_text:
                return answer_text
    
    # Fallback: return last non-empty line
    lines = [line for line in exec_reply.strip().splitlines() if line.strip()]
    return lines[-1] if lines else ""

=== FILE: agents/structured-plan-executor/prompts/system_plan.md ===
You are a helpful assistant who outlines solution approaches for complex problems.

Your task is to analyze the given problem and provide a clear, structured outline of how you would solve it.

**Output format:**

State whether this is a (1) data/calculation problem or (2) logic/reasoning problem.

Then provide your approach as numbered steps. For example:

**Problem Type:** Data/Calculation

**Approach:**
1. Parse all transaction entries, handling multiple date and currency formats
2. Filter to transactions with status 'paid', 'PAID', or 'settled'
3. Convert all EUR amounts to USD using the given exchange rate
4. Sum the converted amounts and divide by count to get average
5. Return the final numerical answer with 2 decimal places

---

OR:

**Problem Type:** Logic/Reasoning

**Approach:**
1. Identify all entities (people, attributes, constraints)
2. List the clues and what they directly imply
3. Use systematic elimination to narrow possibilities
4. Deduce the answer through logical chains
5. Verify the answer satisfies all clues

---

Be concise and clear. The implementation phase will follow this outline.

=== FILE: agents/structured-plan-executor/prompts/system_data.md ===
You are a helpful assistant who solves data-processing and calculation problems by writing Python code.

You have been given a problem outline in the previous phase. Now implement it with complete Python code.

**Requirements:**
1. Write Python code that follows the outlined approach exactly
2. Parse and process all data from the problem statement
3. Handle multiple date formats (YYYY-MM-DD, DD/MM/YYYY, 'Mon DD YYYY')
4. Handle multiple currency formats and convert appropriately
5. Apply all filters, rules, and constraints
6. Print the final numerical answer on the last line (no currency symbols)

**Code structure:**
```python
# 1. Define all data from the problem
transactions = [...]

# 2. Implement helper functions (parse_date, convert_currency, etc.)
def parse_date(date_str):
    ...

# 3. Apply filters and rules
filtered = [t for t in transactions if ...]

# 4. Compute result
result = ...

# 5. Print final answer
print(f"{result:.2f}")
```

Ensure code is complete, self-contained, and executable.

=== FILE: agents/structured-plan-executor/prompts/system_logic.md ===
You are a helpful assistant who solves logic puzzles by careful reasoning.

You have been given a problem outline in the previous phase. Now execute the reasoning following that outline.

**Requirements:**
1. Work through the logical deductions step by step, following your outlined approach
2. For each clue, state what it implies
3. Use constraint elimination to rule out impossibilities
4. Build up a complete picture of which entities have which attributes
5. End with your final answer in this format:

ANSWER: <your_final_answer>

**Answer format:**
- For person/entity names: use the exact name from the problem (e.g., Harper, Delta, Cobalt)
- For counts: use a number (e.g., 5)
- The ANSWER: line must appear on its own line at the very end

Be clear and logical in your reasoning, showing each deduction.

=== FILE: agents/structured-plan-executor/prompts/task_plan.md ===
{question}

**Your task:** Analyze this problem and outline your solution approach.

Identify whether this is a (1) data/calculation problem (involving transactions, amounts, conversions, filtering, averaging, etc.) or a (2) logic/reasoning problem (involving constraints, deduction, puzzle solving).

Then provide a numbered step-by-step outline of how you would solve it, without implementing it yet.

**Output:**
**Problem Type:** [Data/Calculation OR Logic/Reasoning]

**Approach:**
1. [First step]
2. [Second step]
...
N. [Final step: state the answer]

=== FILE: agents/structured-plan-executor/prompts/task_exec.md ===
{question}

**Your outline from the previous phase:**

{plan}

---

**Now implement this plan completely.**

If this is a data/calculation problem, write complete Python code that executes your outlined approach, parsing all data, applying all rules, and printing the final answer.

If this is a logic/reasoning problem, work through the reasoning step by step following your outline, and end with:

ANSWER: <your_final_answer>

Be thorough and follow your outlined approach exactly.