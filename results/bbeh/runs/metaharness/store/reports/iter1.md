# Iteration 1 Post-Eval Report

## Summary
Both candidates from iteration 1 regressed substantially from the seed baseline (62.5%), scoring 45.8% and 41.7% respectively. While they attempted different mechanisms (decomposed subgoals vs. structured reasoning), both failed to improve on the seed.

## Regression Analysis
- **cand_decomposed_subgoals**: -16.7% (45.8%)
- **cand_structured_reasoning**: -20.8% (41.7%)

### Root Causes
1. **Prompt Complexity Overload**: Both candidates added extensive structural guidance (step-by-step decomposition, explicit problem typing, sub-goal enumeration). This increased context cost (+20-23% over seed) while confusing the model rather than aiding it.

2. **Output Truncation**: Analysis of traces shows several responses were incomplete or lacked the required `ANSWER:` line. The added prompt structure encouraged very long model outputs that exceeded reasonable length bounds or caused extraction failures.

3. **Task Mismatch**: The problem-type detection strategies (tracking, pattern, logic, calculation) didn't match actual problem distributions well. Overspecifying decomposition for simple problems added unnecessary overhead.

## Per-Family Performance
Both candidates struggled most on translation tasks (linguini) and object tracking (shuffled_objects), the same weak points as the seed. The elaborate guidance did not help on difficult problem types.

## Takeaway
Structural guidance without supporting mechanisms (memory, multi-turn refinement, or tool use) increases token cost and reduces clarity. Next iteration should: (1) avoid verbose prompt templates; (2) add retrievable external scaffolding rather than inline guidance; or (3) use model calls strategically (e.g., separate planning phase) rather than all-in-one prompts.
