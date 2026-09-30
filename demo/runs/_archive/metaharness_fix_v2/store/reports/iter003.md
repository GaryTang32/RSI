# Iteration 3 Report

## Summary
Two candidates tested: **context_extracted_code_harness** (pre-extracted hints) and **self_critique_code_harness** (code review loop).

## Results
- **context_extracted_code_harness**: **100%**, 1607.42 context cost ✓ NEW BEST (Pareto leader)
  - Perfect accuracy on all 12 numeric tasks
  - Mechanism: Extract key quantities and operations from question; provide as structured hints to guide code generation
  - More efficient than hybrid (3249 tokens): context hints enable concise model responses
  
- **self_critique_code_harness**: **100%**, 2649.67 context cost
  - Also perfect accuracy
  - Two-stage LLM approach (generate → critique) adds token overhead without accuracy gain
  - Mechanism doesn't justify the cost for already-correct code

## Key Observations
1. **Context hints accelerate code generation**: By pre-extracting quantities and operations, model generates tighter code faster
2. **Self-critique adds cost without benefit**: On tasks where code is already correct, critique loop wastes tokens
3. **Axes A/B/D interaction**: Prompt augmentation + extracted state + efficient sizing all contribute to cost reduction
4. **Frontier progresses leftward**: Score is capped at 100%, so focus shifts to efficiency

## Takeaway for Iteration 4
Current best is cost-optimized. Next: explore generalization to non-numeric tasks (dates, text, lists) via new retrieval mechanisms (Axis C), and further cost reduction (Axis D). Avoid repeating A, B, E, F.

---

## Step 1-2: Falsifiable Hypotheses

I studied the top 3 candidates (`context_extracted_code_harness`, `hybrid_code_text_harness`, `self_critique_code_harness`) and their traces. All achieve 100% on numeric tasks via code execution. The frontier pressure is now on:

1. **Generalization**: The harness will run on dates, text, lists, number theory—not just numeric. Current extraction (sequential: code → ANSWER: → last line) may fail on non-numeric answers.
2. **Efficiency**: Cost can still drop via simpler mechanisms.

### **Hypothesis 1: Multi-Strategy Type-Aware Answer Extraction (Axis C, exploration)**

*Claim:* Sequential fallback extraction (code → ANSWER: → last line) assumes numeric output and fails on other types. If we detect the expected answer type (numeric, date, text, list) from the question and apply type-specific extraction patterns in parallel, we generalize better to all short-answer tasks.

*Mechanism walkthrough:*
- Current (evolve-numeric-007, digit sum of 64!): Extract from code output → "324" ✓
- Non-numeric (hypothetical: "What year was X born?"): Current extracts last line, might get narrative; NEW detects date keywords → extracts ISO dates or 4-digit years with higher confidence
- Example (if a date task existed): "Born in 1987" → NEW detects year pattern and extracts "1987" instead of lower-confidence last line

This is genuinely new: replacing sequential fallback with parallel multi-method extraction.

### **Hypothesis 2: Minimal Direct Prompt (Axis D, exploitation)**

*Claim:* `context_extracted` adds overhead by extracting and augmenting context (regex ops, text building). A simpler prompt—no context hints, just question + unified instruction—maintains accuracy while reducing token usage. The model is capable enough to solve without scaffolding.

*Mechanism walkthrough:*
- Current (evolve-numeric-000): Extracts "41", "factorial", "digit sum" → augments prompt → 2012 tokens
- NEW (same task): Minimal prompt: "What is the sum of the decimal digits of 41! (41 factorial)? Provide Python code or output ANSWER: <value>." → expected: ~1400–1600 tokens (no extraction overhead, but model still reasons step-by-step and uses code)

This is exploitation: simplification on axis D without changing the core mechanism.

---

## Step 3: Prototype on Paper

### Candidate 1: pattern_aware_extraction_harness

*Example (evolve-numeric-000, digit sum of 41!):*
- Question: "What is the sum of the decimal digits of 41! (41 factorial)?"
- Detect: answer_type = "numeric" (contains digits, sum, numbers)
- Code execution output: "144"
- Extract numeric: regex `\b\d+\b` → "144" ✓
- Return: "144"

*Hypothetical non-numeric example (dates):*
- Question: "In what year was the first moon landing?"
- Detect: answer_type = "year" (keywords: year, when, date)
- LLM reply: "The Apollo 11 mission landed on July 20, 1969."
- Extract via sequential: last line → "mission landed on July 20, 1969." ✗ (not a clean answer)
- Extract via NEW year pattern: regex `\b(19|20)\d{2}\b` → "1969" ✓
- Return: "1969"

### Candidate 2: lean_prompt_code_harness

*Example (evolve-numeric-007, digit sum of 64!):*
- Remove context extraction entirely
- Prompt: "What is the sum of the decimal digits of 64! (64 factorial)?\n\nProvide Python code in ```python ... ``` or output ANSWER: <value>."
- Model response: (step-by-step reasoning) + code → output "324"
- Extract: same mechanism as `context_extracted` (code blocks, ANSWER: fallback, last line)
- But: no regex extraction, no augmentation, ~200–300 tokens saved per question
- Expected cost: ~1300–1450 tokens (vs. current 1607)

---

## Step 4: Implementation

```json
{"iteration": 4, "candidates": [{"name": "pattern_aware_extraction_harness", "base_system": "context_extracted_code_harness", "hypothesis": "Multi-strategy answer extraction with type detection (numeric, date, text, list) allows better generalization to non-numeric short questions; applying type-specific extraction patterns instead of sequential fallback improves robustness across diverse question types.", "axis": "exploration", "components": ["axis:C (selection/retrieval algorithm with type awareness)"]}, {"name": "lean_prompt_code_harness", "base_system": "context_extracted_code_harness", "hypothesis": "Removing context extraction overhead and using an ultra-lean, unified prompt instruction reduces token usage while maintaining accuracy; the model is capable of generating correct code without augmented scaffolding.", "axis": "exploitation", "components": ["axis:D (resource efficiency via prompt simplification)", "axis:A (simplified prompt template)"]}]}
```

---
