# Iteration 2 Post-Eval Report

## Summary
Two more candidates were tested:
- **code_required**: Mandatory code generation for state-tracking problems (axis F) - 0% accuracy
- **example_retrieval**: Strategy guidance for linguistic problems (axis C) - 0% accuracy

## What Changed
- code_required: Aggressive detection of "tracking problems" (swaps, exchanges), forced pure Python code generation
- example_retrieval: Detected linguini/translation problems, injected structured analysis strategy

## Outcome
**Both failed completely (0% on all 24 test units)**.

## Analysis
Similar root causes as iteration 1:
1. **Problem detection is unreliable**: Both candidates relied on pattern matching (regex) to categorize problems
   - code_required: The tracking_patterns may have incorrectly classified problems (false positives/negatives)
   - example_retrieval: The linguini detection (translate, plural, grammar keywords) may have been too broad
2. **Answer format breakage**: Both likely returned answers in unexpected formats when fallback paths were taken
3. **Complexity cost**: Multi-turn approaches with code execution add complexity without guaranteed benefit

## Specific Issues
- code_required's aggressive code-only request might have caused the model to refuse or produce non-executable code
- example_retrieval's linguini strategy injection may have been irrelevant for non-translation problems, degrading performance

## Takeaway
- **Do not rely on simple pattern matching for problem classification** - it breaks the fallback behavior
- The weak areas (linguini 25%, shuffled_objects 25%) are genuinely hard and may require:
  - More sophisticated detection (not regex-based), or
  - A different approach that works for ALL problems, not just a subset
- Consider techniques that improve the single-call behavior without detection logic
