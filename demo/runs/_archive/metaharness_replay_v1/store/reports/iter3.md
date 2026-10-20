## Iteration 3: Enforcement Fails; Parsing Needed

**Systems tested:**
- `python_enforced_arithmetic`: 25.0%, cost 1697
- `result_variable_extraction`: 8.3%, cost 1980

### Findings
`python_enforced_arithmetic` attempted strict enforcement ("MUST use Python, do NOT manually calculate") but only achieved 25%, worse than seed. Cost improved but score regressed significantly. Likely reason: aggressive tone ("CRITICAL", "MUST") made model anxious and more prone to overthinking—the opposite of intended effect.

`result_variable_extraction` (requiring "Result: <value>" format) achieved only 8.3%—model didn't consistently adopt the format, and extraction failed.

### Rootcause
The real problem: **extraction doesn't distinguish what the model *computed* (Python output, early in response) from what it *claims* (narrative, end of response)**. Model runs `print(result) = 144` then later writes "ANSWER: 169". Extraction currently searches globally and grabs the last/loudest answer (169).

### Takeaway
To break 50%, need two things: (1) **gentle** guidance (not aggressive enforcement), (2) **extraction that prioritizes Python-computed values** over narrative claims. Next iteration should parse code output regions specifically.

---

## Step 1–3: Analysis & Hypotheses

### Frontier Status
- **Best:** `answer_format_smart_extraction` (50%, cost 3429)
- **Second:** `seed` (33.3%, cost 2201)
- **Regressing trend:** iterations 2–3 scored below frontier

### Core Problem
Detailed trace analysis (e.g., evolve-numeric-000):
- Model runs Python: `digit_sum = sum(...)` → output `144` ✓
- Model then manually sums digits: `3+3+4+5+...` → writes `169` ✗
- Extraction grabs narrative `169`, ignores Python `144`

**The harness extracts from the wrong part of the response.**

### Hypothesis 1 (Exploitation, Axis A): Gentle Python Trust

**Falsifiable claim:** By adding *soft* guidance ("Python is your source of truth") without aggressive mandates, and keeping prompts simple, the model gains confidence in its code outputs and commits to them instead of second-guessing with manual arithmetic.

**Mechanism:** 
- System: brief, encouraging tone ("use Python as your source of truth")
- Prompt: simple ("use Python, state final answer clearly")
- Extraction: same smart regex as base

**Why this differs from iter3:** Gentle persuasion vs. aggressive enforcement; fewer words, no verbose scaffolding.

### Hypothesis 2 (Exploration, Axes B + C): Prioritized Python Value Extraction

**Falsifiable claim:** By parsing responses to identify *code output blocks* and extracting numeric values from them first (before ANSWER tags or narrative), we bypass the model's narrative errors entirely. Storage of extracted Python values in memory makes them the authoritative source.

**Mechanism:**
- Extract lines matching patterns like `"result: X"`, `"= X"` (code-output style)
- Store these extracted values
- Prioritize stored Python values in final extraction over ANSWER tags
- Decouple what model *computed* from what it *claims*

**Why different:** New retrieval algorithm that respects code-output locality instead of global text search.

---

## Step 4: Implementation

```json
{
  "iteration": 4,
  "candidates": [
    {
      "name": "gentle_python_trust",
      "base_system": "answer_format_smart_extraction",
      "hypothesis": "By adding gentle, encouraging guidance that Python is the source of truth and keeping prompts minimal (no aggressive mandates or verbose scaffolding), the model gains confidence in its code outputs and reports them instead of second-guessing with wrong manual arithmetic.",
      "axis": "exploitation",
      "components": ["axis:A_gentle_prompt_guidance", "axis:A_prompt_minimalism"]
    },
    {
      "name": "python_priority_extraction",
      "base_system": "answer_format_smart_extraction",
      "hypothesis": "By extracting numeric values that appear in code-output patterns ('result: X', 'X =', lines with descriptive colons) and prioritizing them over ANSWER tags or narrative, we bypass the model's narrative override errors and use only what was actually computed.",
      "axis": "exploration",
      "components": ["axis:C_code_output_prioritization", "axis:B_extracted_value_storage"]
    }
  ]
}
```
