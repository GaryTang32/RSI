# Iteration 2 Post-Eval Report

## Summary
Iteration 2 tested two different mechanisms: code generation for structured problems, and error recovery for formatting failures. Both regressed from 62.5% baseline.

## Results
- **cand_code_solver**: 29.2% (-33.3 points), cost +17766.875, steps=2.0
- **cand_error_recovery**: 50.0% (-12.5 points), cost +20371.458, steps=1.125
- Wildly different outcomes despite similar cost increases

## Key Findings

**cand_code_solver failures**: The problem detection heuristic (checking for keywords like "true/false", "max/min", "count") misidentified many problems as "code-solvable". This broke:
- Boolean expressions: 75% → 25% (code tried to evaluate but failed)
- Web of lies: 75% → 0% (completely broken)
- Shuffled objects: 25% → 0% (completely broken)
- Only saved cost on causal understanding and one linguini case

The root issue: using keyword matching to detect problem type is too coarse-grained and applies code-generation incorrectly to problems that need textual reasoning.

**cand_error_recovery results**: Mixed. It preserved most of baseline's successes but:
- Web of lies: 75% → 0% (catastrophic regression on all 4 cases)
- Shuffled objects: 25% → 25% (one case now solved)
- Cost increased by ~1.3x due to recovery attempts

The recovery mechanism appears to confuse linguistic/logical reasoning problems where "re-formatting" the answer makes no sense.

## Takeaway
Problem-specific strategies are risky without proper detection. Error recovery (asking for explicit format) helps when the problem is formatting but hurts when the problem is reasoning. Future work should: (1) test hypotheses on actual failing examples first, (2) avoid global problem detection—be more conservative about when to apply special handling, (3) focus on mechanisms that help weak areas (linguini, shuffled_objects) without breaking strong areas.
