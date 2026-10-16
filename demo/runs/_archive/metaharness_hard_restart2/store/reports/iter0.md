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
