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
