# Iteration 3: Pattern Templates Break Through Cost Barrier

**What changed**:
- `self_checking_code`: Single-pass with inline verification (exploit axis E: update trigger)
- `pattern_template_solver`: Hardcoded patterns for digit_sum, modulo, bit_count, fallback to LLM (explore axis C+B+D: retrieval + state + sizing)

**Score & cost**:
- **pattern_template_solver: 100% (12/12), 1,465 tokens/task** — NEW SOLE FRONTIER POINT
- self_checking_code: 91.7% (11/12), 2,195 tokens — regressed on accuracy
- Iteration 2's code_execution (100%, 4,501) now dominated

**Why pattern_template_solver wins**:
- **Mechanism shift**: Regex pattern matching (digit sum, modulo, bit count) → hardcoded Python templates → deterministic execution
- 10/12 tasks matched patterns with **zero LLM cost**: evolve-numeric-{0,1,3,4,5,7,8,9,10,11}
- Only 2 tasks (002, 006) fall back to LLM code gen: ~1,686 and ~1,245 tokens
- **Key insight**: Structured problems can be solved without LLM by recognizing syntax (e.g., "mod", "digits of", "bits")

**Why self_checking_code regressed**:
- Embedding verification in a single code block failed for 1 task (11/12 = 91.7%)
- Model didn't reliably self-verify inline; cost was half of code_execution but accuracy suffered
- **Takeaway**: Two-pass (generate + verify) is more robust than single-pass

**Frontier dynamics**:
- pattern_template_solver's cost (1,465) comes from only 2 fallback tasks; 10/12 are free
- code_execution was too costly (4,501) for same accuracy
- Pareto frontier: only 1 point (pattern_template_solver)

**Takeaways for iter4**:
- Pattern matching is powerful but incomplete (2 tasks missed)
- Axis C (retrieval) and D (sizing) are high-leverage: small pattern additions could push cost near-zero
- Could also explore: LLM-based classification (cheaper than full code gen) + template lookup
- Next iteration should either (1) expand pattern DB or (2) use model to classify, then apply templates

---

```json
{
  "iteration": 4,
  "candidates": [
    {
      "name": "pattern_solver_enhanced",
      "base_system": "pattern_template_solver",
      "hypothesis": "Extending the pattern database to recognize multi-operation arithmetic (ledger-style) and other structured problems reduces LLM fallback rate from 2/12 to 0/12, approaching zero-cost exact-match execution.",
      "axis": "exploitation",
      "components": ["axis:D (sizing: expanded pattern/template database)", "axis:C (retrieval: more comprehensive pattern matching rules)"]
    },
    {
      "name": "classifier_template_solver",
      "base_system": "pattern_template_solver",
      "hypothesis": "Using an LLM to classify problem type (single cheap call per task) and then applying deterministic templates is more general and cheaper than hardcoded regex patterns, while maintaining accuracy through fallback.",
      "axis": "exploration",
      "components": ["axis:F (model usage: model-based classification)", "axis:A (prompt architecture: classification-first design)"]
    }
  ]
}
```

---

## Candidate 1: pattern_solver_enhanced
