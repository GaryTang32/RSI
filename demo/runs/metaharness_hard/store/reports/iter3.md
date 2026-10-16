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
