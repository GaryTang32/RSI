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
