**Iteration 2 Results:**
- `compute_then_extract` (0%): Completely failed due to broken Python output regex in extraction. Pattern `<invoke name="(?:python|bash)"` doesn't match Claude's actual tool format with nested parameters.
- `multi_check_verify` (66.7%, +25% vs smart_extraction): **New best system**. Two-stage solving (initial + independent re-verification) recovers 8/12 correct, at high cost (7199 tokens, 2x smart_extraction).

**Failures persist in multi_check_verify (4 wrong):**
- evolve-numeric-000: 129 vs 144 (41! digit sum) — computational error
- evolve-numeric-004: 92 vs 100 (52^12 digit sum) — computational error  
- evolve-numeric-005: 297 vs 351 — computational error
- evolve-numeric-007: 261 vs 324 — computational error

**Key insight:** Even with independent re-verification, the model repeats similar arithmetic mistakes. The bottleneck is **upstream computation quality**, not verification strategy.

**Takeaway:** To push beyond 66.7%, either (1) improve prompting to force more reliable computation (Python-first, stricter constraints), or (2) use ensemble methods (multiple independent attempts + voting) to mask occasional errors.

---

## Hypotheses for Iteration 3

**Hypothesis 1 (Exploitation):** Strengthening the requirement for Python computation in both stages and fixing extraction of tool output (which was broken in compute_then_extract) will reduce arithmetic errors and improve the 66.7% baseline.

**Hypothesis 2 (Exploration):** Running multiple independent solve attempts and selecting the most common extracted answer (ensemble voting) will catch occasional computation errors through redundancy, achieving accuracy gains without model modification.

---

## Prototype Walkthroughs

### Candidate 1: python_constrained_multi_check

**Mechanism:** Enforce Python-first computation in both verification stages; extract primarily from tool output.

**Example (evolve-numeric-000: 41! digit sum, expected 144):**
- Stage 1 prompt: "Solve via Python. Show code that computes and prints the answer."
- Model writes and runs: `print(sum(int(d) for d in str(math.factorial(41))))` → output: 129
- Extract "129" from Python output
- Stage 2 prompt: "You got 129. Re-solve independently using Python. Only trust code output."
- Model re-runs same or similar code → output: 129 (consistent, but WRONG—model's underlying computation is faulty)
- Return 129 (fails, but this is a hard case where model's math is wrong)

**For a case that should improve (evolve-numeric-001: mod operation, expected 91):**
- Stage 1: Model tries direct modulo → 7 (wrong)
- Extract 7
- Stage 2: Model re-thinks, uses CRT method → 91 (correct, as seen in traces)
- Return 91 (succeeds via re-reasoning)

**Key difference from multi_check_verify:** Prompts explicitly say "trust only Python output, not your text summary" and extraction prioritizes code output.

---

### Candidate 2: ensemble_voting_simple

**Mechanism:** Three independent solves, extract answer from each, return the most frequent answer (or first if all different).

**Example (evolve-numeric-000: 41! digit sum, expected 144):**
- Attempt 1: Model computes, gets 129, extracts 129
- Attempt 2: Model computes (independent state), gets 129, extracts 129  
- Attempt 3: Model computes, might try different approach, could get 129 or 144
- If 2+ return 129, return 129 (fails, but **failure mode is less isolated**)

**For a case that should improve (evolve-numeric-001: mod 148, expected 91):**
- Attempt 1: Direct modulo → 7
- Attempt 2: Direct modulo again → 7
- Attempt 3: Uses CRT reasoning → 91
- Majority is 7 (fails by vote), OR voting rule could prefer "confidence" of each method
- **Refinement:** weight by extraction confidence (ANSWER: format > output line > last number)

**Cost:** ~3650 tokens/task (3x seed cost ≈ 1525), vs 7199 for multi_check_verify. Potentially cheaper per point if accuracy improves.

---

```json
{
  "iteration": 3,
  "candidates": [
    {
      "name": "python_constrained_multi_check",
      "base_system": "multi_check_verify",
      "hypothesis": "Enforcing Python computation as the sole source of truth in both solve and verify stages, with extraction prioritizing tool output, will eliminate text-based misreporting and reduce arithmetic errors.",
      "axis": "exploitation",
      "components": ["axis:A", "axis:C"]
    },
    {
      "name": "ensemble_voting_simple",
      "base_system": "seed",
      "hypothesis": "Three independent solve attempts with majority-vote answer selection will catch occasional computational errors through redundancy and improve accuracy at lower cost than multi-stage verification.",
      "axis": "exploration",
      "components": ["axis:E", "axis:B", "axis:C"]
    }
  ]
}
```

---

## Files
