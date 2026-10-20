## Iteration 2: Verification & Structure

**Systems tested:**
- `verify_calculations`: 25.0% (-8.3 from seed), cost 2246
- `structured_numeric_solver`: 16.7% (-16.7 from seed), cost 2163

### Analysis
Both candidates **regressed significantly** from seed (33.3%, cost 2201). Root cause: both added complexity (verification phrases, 5-step templates, longer prompts) that *increased* the model's tendency to use narrative reasoning instead of trusting Python tool outputs.

The frontier winner from iter1 (`answer_format_smart_extraction`, 50%) actually used Python correctly in many traces but then reported wrong arithmetic manually afterward. Iteration 2's emphasis on "verification" and "structured steps" made this worse, not better.

### Key Finding
All remaining failures are **arithmetic errors in narrative**, not code errors:
- Model computes 41! correctly via Python but then manually sums digits as 169 (correct: 144)
- Model runs modular arithmetic code but then writes wrong final answer
- The issue is *reporting* the Python result, not computing it

### Takeaway
To improve beyond 50%, focus on: (1) forcing Python for arithmetic (no manual math), (2) making Python outputs the authoritative answer source, (3) keeping prompts **simple and direct**—less structure beats more.

---

## Step 1–3: Hypotheses & Paper Walkthrough

### Frontier Status
- **Best:** `answer_format_smart_extraction` (50%, cost 3429)
- **Next best:** seed (33.3%, cost 2201)
- **Regressed:** verify_calculations, structured_numeric_solver

### Hypothesis 1 (Exploitation, Axis A): Enforce Python-only arithmetic
**Claim:** By explicitly forbidding manual arithmetic and requiring Python for *every* numeric operation (sums, products, digit counting), the model will report Python outputs instead of hallucinating. Same extraction logic, higher accuracy.

**Paper test on evolve-numeric-000:**
- Current: Model runs Python, gets 144, but then manually re-sums as 169 → extract "169" → fail
- With enforcement: Prompt says "Do NOT manually add. Use Python code: `digit_sum = sum(...)`". Model outputs Python result → extract "144" → success

### Hypothesis 2 (Exploration, Axes A+C): Result-variable pairing
**Claim:** By requiring the model to state answers as "Result: <value>" where value is a Python variable, and extracting from "Result:" pattern first, we decouple narrative from code. Even if the model writes wrong arithmetic in narrative, the code result is extracted.

**Paper test on evolve-numeric-000:**
- Current: Narrative says "= 169", ANSWER: 169 → fail
- With pairing: Prompt asks for "Result: <computed_value>". Model writes:
  ```
  Python computed: digit_sum = 144
  Result: 144
  Narrative explanation: ... (could be wrong) ...
  ANSWER: 169 (wrong narrative)
  ```
- Extraction: Look for "Result:" first → extract 144 → success

Both stay at 1 LLM call, no cost explosion. Hypothesis 1 is lighter (prompt guidance only). Hypothesis 2 is more exploratory (new extraction mechanism + prompt format).

---

```json
{
  "iteration": 3,
  "candidates": [
    {
      "name": "python_enforced_arithmetic",
      "base_system": "answer_format_smart_extraction",
      "hypothesis": "By explicitly requiring Python code for all arithmetic operations (never manual calculation) and removing looser guidance, the model will report Python output directly, eliminating calculation errors that stem from narrative re-derivation.",
      "axis": "exploitation",
      "components": ["axis:A_prompt_enforcement_python_only"]
    },
    {
      "name": "result_variable_extraction",
      "base_system": "answer_format_smart_extraction",
      "hypothesis": "By requiring the model to state answers as 'Result: <value>' where value is a Python variable, and extracting from that pattern first, we decouple narrative reasoning from code output, preventing errors where the model narrates wrong arithmetic after correct Python.",
      "axis": "exploration",
      "components": ["axis:A_result_format", "axis:C_variable_based_extraction"]
    }
  ]
}
```

---
